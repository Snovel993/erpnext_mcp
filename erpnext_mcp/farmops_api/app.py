# SPDX-License-Identifier: MIT
"""The WSGI application: read the request, authenticate, delegate, answer JSON.

    POST /farmops/api/mobile/<method>
    POST /farmops/api/files/<method>
    X-FarmOps-Token: <api_key>:<api_secret>
    Content-Type: application/json

────────────────────────────────────────────────────────────────────────────
THE ONE PROMISE THIS FILE MAKES
────────────────────────────────────────────────────────────────────────────

**EVERY ANSWER IS `application/json`. THERE IS NO PATH OUT OF HERE THAT RENDERS
HTML AND NO PATH THAT REDIRECTS.** That is not a style preference, it is the
entire reason the service exists: v0.17.x's failure was a phone receiving HTTP
200 and the Desk's login page, and the app reporting it as a decoding error —
the least useful true thing it could have said. A 404, a 405, a 401, an
unhandled `KeyError` in a tool three layers down: all of them leave this module
as a JSON object with a status code, or the release has not fixed anything.

`_failure` is the only way a non-2xx leaves this file, and `dispatch` is wrapped
so that even a bug in this module's own error handling produces JSON.

────────────────────────────────────────────────────────────────────────────
THE ERROR ENVELOPE IS FRAPPE'S, BECAUSE THE APP ALREADY READS FRAPPE'S
────────────────────────────────────────────────────────────────────────────

`FrappeClient.serverMessage` — the shipped iOS build, already in TestFlight —
looks for `_server_messages` first (a JSON string holding an array of JSON
strings, each with a `message` key), then `exception`, then `message`. So a
refusal from here emits `_server_messages` in exactly that shape, and the
sentence a wrapper was written to say reaches the phone as itself:

    "A reason is required to hand a task back. 'The ladder is broken and I
     could not reach the detector' is a fact somebody can act on…"

`error` is emitted alongside it because the v0.18.0 brief asks for `{"error": …}`
and because a `curl` is easier to read that way. Three keys, one message, no
client change.

────────────────────────────────────────────────────────────────────────────
THE STATUS CODES ARE A CONTRACT WITH THE APP, NOT DECORATION
────────────────────────────────────────────────────────────────────────────

`FarmOpsKit` treats **401 as "this credential is dead — sign out"**, which
discards the offline queue. Everything else it treats as "still signed in, try
later". So the mapping below is deliberate and narrow:

  * **401** — and only — for a credential that did not verify.
  * **403** for the role gate, the enrolment gate and entity scoping: refused,
    but the login is real and the queued day's work is not thrown away.
  * **404** for a docname that does not exist *or* is not this caller's, which
    `guard.require_scoped_doc` deliberately makes indistinguishable.
  * **429** rate limit, **503** kill switch — the two that must never be 401,
    or one flipped switch signs forty phones out and loses what is on them.
  * **400** for everything else a caller can correct, **500** for anything else,
    with the detail in the log and not in the answer.
"""

from __future__ import annotations

import json
import logging
import traceback

import frappe
from werkzeug.wrappers import Request, Response

from .. import __version__
from ..api import guard
from . import auth, session
from .routes import BY_PATH, PREFIX, bind

logger = logging.getLogger("farmops_api")

#: Where the service listens inside the container. Nothing new is published to
#: the LAN — see `RELEASES/v0.18.0.md` for how the funnel reaches it.
DEFAULT_PORT = 5250

#: Answered unauthenticated, and says nothing about the site. It exists so that
#: "the funnel path is wrong" and "the credential is wrong" are two different
#: answers at the point somebody is standing in a field trying to tell them
#: apart. It touches no database and opens no Frappe session.
HEALTH_PATH = f"{PREFIX}/health"

#: The same sentence for every way authentication can fail — malformed header,
#: unknown key, wrong secret, disabled account. A caller learns whether it is
#: in, and nothing else; telling it which fact was wrong hands it a free oracle
#: for the one fact worth probing.
UNAUTHORIZED = (
	"This request carried no usable Farm Ops credential. Send "
	"`X-FarmOps-Token: <api_key>:<api_secret>` from the login QR. Nothing was read "
	"and nothing was changed."
)

_MAX_BODY = auth.MAX_BODY_BYTES


# ── the answer shapes ───────────────────────────────────────────────────────
def _json(payload: dict, status: int = 200) -> Response:
	"""One JSON response. The only kind this module knows how to build."""
	return Response(
		json.dumps(payload, default=str),
		status=status,
		content_type="application/json",
		# A JSON API has no business being sniffed as anything else, and this
		# service is reachable from the public internet through the funnel.
		headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
	)


def _success(data) -> Response:
	"""`{"message": …}` — the envelope `FrappeClient.unwrapMessage` unwraps.

	Identical to what the whitelisted path returns for the same call, because it
	IS the same call: `routes.py` delegates to the `@guard.endpoint` function and
	this wraps its return value the one way Frappe would have wrapped it.
	"""
	return _json({"message": data})


def _failure(status: int, message: str, exception: str = "") -> Response:
	"""A refusal the phone can read, in the three places it looks for one."""
	return _json(
		{
			"error": message,
			"exception": exception or message,
			# Frappe's own shape: a JSON *string* containing an array of JSON
			# *strings*, each an object with a `message`. Nested twice, which
			# looks like a mistake and is not — `serverMessage` decodes exactly
			# this, and a flat array would silently produce a phone that shows
			# "Something went wrong" instead of the sentence somebody wrote.
			"_server_messages": json.dumps([json.dumps({"message": message})]),
		},
		status=status,
	)


# ── mapping a refusal to a status ───────────────────────────────────────────
#: What the phone is told when something failed that nobody wrote a sentence for.
#: Deliberately says nothing specific — see `_message_for`.
INTERNAL = (
	"The farm server could not complete that. It has been logged. Nothing was "
	"changed — try again, and if it keeps happening tell the office."
)


def _status_for(exc: Exception) -> int:
	"""Which deliberate refusal this is, or **0 for one nobody anticipated**.

	THE ZERO IS THE POINT AND IT IS NOT A STATUS. "Which HTTP code" and "did
	somebody mean this" are two different questions, and answering the second
	with `>= 500` gets 503 wrong: the kill switch is the most deliberate refusal
	in the whole system and it carries a sentence an operator wrote for a worker
	to read — "the Farm Ops mobile API is switched off on this site" — which a
	500-shaped test would replace with "something went wrong". Asking the
	question separately is what keeps that sentence on the phone.

	ORDER MATTERS BELOW. `MobileDisabled` and `RateLimited` both subclass
	`frappe.ValidationError`, so they have to be asked about before it, or a
	tripped kill switch answers 400 and the app shows a broken request instead
	of "the office turned it off — keep working".
	"""
	if isinstance(exc, guard.MobileDisabled):
		return 503
	if isinstance(exc, guard.RateLimited):
		return 429
	if isinstance(exc, frappe.PermissionError):
		return 403
	if isinstance(exc, frappe.DoesNotExistError):
		return 404
	if isinstance(exc, frappe.ValidationError):
		return 400
	return 0


def _message_for(exc: Exception, anticipated: int) -> str:
	"""What to say. An UNANTICIPATED failure says nothing specific, on purpose.

	A refusal somebody wrote carries its own sentence, because those sentences
	were written to be read in a field — they name the argument, the record and
	the fix. An exception nobody anticipated carries none of it: the traceback
	goes to the log where an operator can read it, and the phone gets a fixed
	line, because an internal error message is where table names, file paths and
	query fragments leak out to whoever is holding the phone.
	"""
	if not anticipated:
		return INTERNAL
	return str(exc) or "That request could not be completed."


# ── the request ─────────────────────────────────────────────────────────────
def _body(request: Request) -> dict:
	"""The request body as a dict, or `{}`. NEVER raises, never reads a huge one.

	A body that is not JSON is `{}` rather than a 400: the eleven methods all
	have defaults for everything except what their own validation requires, and
	an unauthenticated caller sending nonsense should meet the 401 rather than a
	parser error that confirms the path exists.
	"""
	try:
		if request.content_length and int(request.content_length) > _MAX_BODY:
			return {}
		raw = request.get_data(cache=False, as_text=True) or ""
	except Exception:  # pragma: no cover - a truncated or hostile body
		return {}
	if not raw.strip().startswith("{"):
		return {}
	try:
		parsed = json.loads(raw)
	except Exception:
		return {}
	return parsed if isinstance(parsed, dict) else {}


def dispatch(request: Request) -> Response:
	"""One request, start to finish. Returns a JSON response for every outcome."""
	path = (request.path or "").rstrip("/") or "/"

	if path == HEALTH_PATH:
		# GET or POST, unauthenticated, and deliberately incurious: it proves
		# this process is answering on this path and says nothing else.
		return _json({"ok": True, "service": "farmops-api", "version": __version__})

	if not path.startswith(f"{PREFIX}/"):
		return _failure(404, f"{path} is not a Farm Ops API path.")

	route = BY_PATH.get(path[len(PREFIX) :])
	if route is None:
		# The same 404 for a path that does not exist and one that is not
		# reachable, which here is the same fact: the surface is eleven routes
		# and there is nothing else behind this prefix.
		return _failure(404, f"{path} is not a Farm Ops API method.")

	if request.method != "POST":
		# Every method is POST, including the reads. The whitelisted path
		# accepted GET on the reads and this one does not: a GET carries its
		# arguments in a URL, and a URL is the one part of a request that gets
		# written to an access log by every proxy between here and a phone.
		return _failure(405, f"{path} is POST only.")

	body = _body(request)

	with session.request_session(request=request, body=body):
		user, source = auth.resolve(request.headers, body)
		if not user:
			# Nothing was opened as anybody, so there is nothing to roll back.
			# The refusal is not audited HERE because `guard` audits per method
			# on an authenticated caller — an unauthenticated flood must not be
			# able to grow MCP Action Log, which is the same argument v0.17.2
			# made for metering failures by key rather than logging them.
			logger.info("farmops-api 401 %s from %s", path, request.remote_addr)
			return _failure(401, UNAUTHORIZED)

		session.become(user)
		try:
			result = route.handler(**bind(route, body))
		except Exception as exc:
			anticipated = _status_for(exc)
			# `guard` already rolled back and committed its own audit row on
			# every refusal it raised. Rolling back again is harmless and covers
			# the exceptions it did not raise — a tool that threw halfway
			# through a write must not leave the half behind.
			session.rollback()
			if anticipated:
				logger.info("farmops-api %s %s user=%s: %s", anticipated, path, user, exc)
			else:
				logger.error(
					"farmops-api 500 %s user=%s\n%s", path, user, traceback.format_exc()
				)
			return _failure(
				session.status_hint(anticipated or 500),
				_message_for(exc, anticipated),
				type(exc).__name__,
			)

		# See `session.py`: reads commit too, because `_stamp_last_seen` writes
		# from inside one and the idle-grant sweep is what that stamp is for.
		session.commit()
		logger.info("farmops-api 200 %s user=%s door=%s", path, user, source or "header")
		return _success(result)


# ── the WSGI callable ───────────────────────────────────────────────────────
def create_app():
	"""The WSGI application. Nothing is configured at construction time.

	No state is captured in the closure on purpose: every request opens and
	closes its own Frappe session, so two gunicorn workers, two threads and two
	successive calls on one thread all behave identically and none of them can
	read another's identity.
	"""

	@Request.application
	def app(request: Request) -> Response:
		try:
			return dispatch(request)
		except Exception:  # pragma: no cover - the promise at the top of the file
			# A bug in THIS module still answers JSON. The alternative is
			# Werkzeug's own HTML 500 page, which is the exact failure — an
			# HTML body where the app expected JSON — that this release exists
			# to end. It would be perverse to reintroduce it in the handler.
			logger.error("farmops-api unhandled\n%s", traceback.format_exc())
			return _failure(500, INTERNAL)

	return app


#: What gunicorn is pointed at. See `supervisord.conf` in the image build.
application = create_app()
