# SPDX-License-Identifier: MIT
"""Fetching a trained model out of Volume Vision. v0.59.0.

WHAT THIS EXISTS TO REPLACE. Getting a model from the training server onto a
phone took an operator, a laptop, `curl`, a `bench console` and a base64 encode
— every step manual, every step forgettable, and the one that actually mattered
(do the labels in the file match the labels on the record?) not checked by
anybody. `tools/ml_model.py`'s `pull_model_from_vv` is the one call that does
it; this module is the half that speaks HTTP.

TWO ENDPOINTS, AND THE ORDER IS THE POINT. Volume Vision's
`/training/models/<uuid>/download` has served raw weights to LiDAR Capture and
BucketLog for as long as those apps have existed, and the bundle work does not
touch it. `/training/models/<uuid>/bundle` is the NEW endpoint that returns the
zip with `manifest.json` in it. This module asks for the bundle FIRST and falls
back to the raw download when the server answers 404/405/501 — which is exactly
what a Volume Vision that has not had the Phase 1 export deployed yet will
answer. The fallback is reported, loudly, in the result: a raw file attached
this way has no manifest, so its `class_names` are still whatever somebody typed
on the record, and the operator needs to know that is what they got.

────────────────────────────────────────────────────────────────────────────
THIS MAKES AN OUTBOUND REQUEST FROM INSIDE THE SITE'S NETWORK
────────────────────────────────────────────────────────────────────────────

Which is the shape of every server-side request forgery there has ever been, and
is why `tools/funnel.py` keeps an allowlist. The position here is different
because the target is different: Volume Vision is an operator's own training box
on their own LAN — `umbrel.local:5101` — so an allowlist of public suffixes
would be exactly wrong, and hardcoding the host would be the "script for one
site" this app is not. What is enforced instead:

  * http/https only. Not `file://`, not `gopher://`, not a scheme at all past
    those two.
  * No credentials in the URL. A `user:pass@host` target is refused rather than
    forwarded, because the one thing worse than fetching from a strange host is
    handing it something on the way.
  * No redirects followed. A 3xx is reported with its `Location` and nothing is
    fetched — following one is how an allowed host turns into an unallowed one
    between the check and the request.
  * A size ceiling, checked against `Content-Length` before the body is read and
    against the body after, so a server that lies about the first does not get
    to exhaust this worker's memory on the second.
  * The base URL comes from the ML Model record's own `source_server` unless a
    caller passes one explicitly, and only a caller who already holds a
    model-registry role can pass one — see `tools/ml_model.py`.

`requests` is imported INSIDE the fetch, the same way `services/weather.py`
does it, so a bench somehow missing the package loses this one tool with a
sentence saying so rather than failing to import the app.
"""

from __future__ import annotations

import re
import urllib.parse

from ..errors import ToolError

#: Volume Vision's NEW endpoint — the zip with `manifest.json` in it.
BUNDLE_PATH = "/training/models/{uuid}/bundle"

#: Volume Vision's ORIGINAL endpoint, unchanged since before any of this and
#: still what LiDAR Capture and BucketLog pull from. The fallback, never the
#: first choice.
DOWNLOAD_PATH = "/training/models/{uuid}/download"

#: Statuses that mean "this server does not have that endpoint", as opposed to
#: "that model does not exist". A Volume Vision without the Phase 1 export
#: deployed answers one of these to /bundle; Flask's own default for an
#: unrouted path is 404 and Werkzeug's for a method it does not accept is 405.
ENDPOINT_ABSENT_STATUSES = (404, 405, 501)

#: How long to wait. Long compared with `funnel.py`'s 8s probe because this is a
#: multi-megabyte transfer over a LAN and not a liveness check, and short enough
#: that a tool call against an unreachable box returns a sentence rather than
#: hanging until something upstream gives up.
DEFAULT_TIMEOUT_SECONDS = 120

#: The largest model this will pull into memory. A CoreML segmentation bundle is
#: tens of megabytes; 512 MB is far past anything real and still a ceiling, so a
#: misconfigured URL that returns a disk image does not take the worker with it.
MAX_BYTES = 512 * 1024 * 1024

#: Volume Vision reports these beside the bundle for quick inspection — see the
#: Phase 1 plan. Read when present, never required: a fallback download has
#: neither, and a bundle's own `manifest.json` is authoritative over both.
HEADER_VERSION = "X-Model-Version"
HEADER_CLASS_NAMES = "X-Class-Names"

_UUID_PATTERN = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_FILENAME_PATTERN = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', re.IGNORECASE)


# ── the base URL ──────────────────────────────────────────────────────────


def normalise_base_url(raw: str) -> str:
	"""`raw` as an http(s) origin with no trailing slash, or a ToolError saying why not.

	A `source_server` is typed by a person and gets typed as all of
	`umbrel.local:5101`, `http://umbrel.local:5101`, and
	`http://umbrel.local:5101/` — the first is what somebody writes when they
	are thinking of a host and a port rather than a URL. A bare host is read as
	http, because Volume Vision on a LAN is not behind TLS and refusing it would
	be refusing the common case over a prefix.
	"""
	value = str(raw or "").strip().rstrip("/")
	if not value:
		raise ToolError(
			"no Volume Vision server to pull from. Pass source_server, or set it on the ML Model "
			"record with update_model(source_server='http://umbrel.local:5101'). Nothing was fetched."
		)
	if "://" not in value:
		value = f"http://{value}"

	parsed = urllib.parse.urlsplit(value)
	if parsed.scheme not in ("http", "https"):
		raise ToolError(
			f"source_server {raw!r} is a {parsed.scheme or 'schemeless'} URL; only http and https "
			"are fetched from. Nothing was fetched."
		)
	if parsed.username or parsed.password or "@" in parsed.netloc:
		raise ToolError(
			"source_server carries credentials in the URL. This tool will not forward them — put "
			"the host and port in source_server and nothing else. Nothing was fetched."
		)
	if not parsed.hostname:
		raise ToolError(f"source_server {raw!r} has no host in it. Nothing was fetched.")
	if parsed.query or parsed.fragment:
		raise ToolError(
			f"source_server {raw!r} has a query string or fragment on it. It should be an origin — "
			"scheme, host and port. Nothing was fetched."
		)

	base = f"{parsed.scheme}://{parsed.netloc}"
	if parsed.path.strip("/"):
		# A Volume Vision published under a path prefix by a reverse proxy is a
		# real deployment, so the prefix is kept — it is the endpoint paths that
		# are appended to it, not a replacement for it.
		base += "/" + parsed.path.strip("/")
	return base


def bundle_url(base_url: str, uuid: str) -> str:
	return normalise_base_url(base_url) + BUNDLE_PATH.format(uuid=_checked_uuid(uuid))


def download_url(base_url: str, uuid: str) -> str:
	return normalise_base_url(base_url) + DOWNLOAD_PATH.format(uuid=_checked_uuid(uuid))


def _checked_uuid(uuid: str) -> str:
	value = str(uuid or "").strip()
	if not value:
		raise ToolError(
			"source_uuid is required — it is Volume Vision's own TrainedModel.uuid and the only "
			"thing that names a model there. Nothing was fetched."
		)
	if not _UUID_PATTERN.match(value):
		raise ToolError(
			f"source_uuid {value!r} is not a well-formed UUID. It is put straight into the URL this "
			"tool fetches, so it is checked rather than trusted. Nothing was fetched."
		)
	return value


# ── the fetch ─────────────────────────────────────────────────────────────


def _get(url: str, timeout: int):
	"""One GET, no redirects. Returns the response, or raises a ToolError naming what went wrong."""
	try:
		import requests
	except Exception as exc:  # pragma: no cover - Frappe depends on requests
		raise ToolError(
			f"the `requests` package is not importable on this bench ({exc}), so nothing can be "
			"pulled from Volume Vision. attach_model_file with a staged file is the manual path. "
			"Nothing was fetched."
		) from None

	try:
		return requests.get(url, timeout=timeout, allow_redirects=False, stream=False)
	except Exception as exc:
		raise ToolError(
			f"{url} did not answer ({type(exc).__name__}: {exc}). Volume Vision is reached from "
			"this site's own network — check the host and port in source_server, and that the "
			"training server is up. Nothing was fetched."
		) from None


def _body(response, url: str) -> bytes:
	status = int(getattr(response, "status_code", 0) or 0)
	if 300 <= status < 400:
		location = (getattr(response, "headers", {}) or {}).get("Location") or "nowhere it named"
		raise ToolError(
			f"{url} answered {status} redirecting to {location}. This tool does not follow "
			"redirects — a target that moves between the check and the request is how an outbound "
			"fetch ends up somewhere nobody allowed. Point source_server at the final URL. "
			"Nothing was fetched."
		)

	headers = getattr(response, "headers", {}) or {}
	declared = headers.get("Content-Length")
	try:
		declared = int(declared) if declared is not None else None
	except (TypeError, ValueError):
		declared = None
	if declared is not None and declared > MAX_BYTES:
		raise ToolError(
			f"{url} says it is {declared} bytes, past this tool's {MAX_BYTES}-byte ceiling. That is "
			"not a model file. Nothing was fetched."
		)

	content = getattr(response, "content", None)
	if content is None:
		raise ToolError(f"{url} answered {status} with no body at all. Nothing was fetched.")
	content = bytes(content)
	if len(content) > MAX_BYTES:
		raise ToolError(
			f"{url} returned {len(content)} bytes, past this tool's {MAX_BYTES}-byte ceiling. "
			"Nothing was attached."
		)
	if not content:
		raise ToolError(f"{url} answered {status} with an empty body. Nothing was attached.")
	return content


def _file_name(response, url: str, uuid: str, fallback_suffix: str) -> str:
	"""What to call the File this becomes.

	`Content-Disposition` first — it is what Volume Vision itself calls the
	artefact — falling back to a name built from the uuid, so a bundle is never
	stored under a name that says nothing about which trained model it is.
	"""
	headers = getattr(response, "headers", {}) or {}
	disposition = str(headers.get("Content-Disposition") or "")
	match = _FILENAME_PATTERN.search(disposition)
	if match:
		candidate = urllib.parse.unquote(match.group(1)).strip().replace("\\", "/")
		candidate = candidate.rsplit("/", 1)[-1]
		if candidate and candidate not in (".", ".."):
			return candidate
	return f"{uuid}{fallback_suffix}"


def fetch_model(
	base_url: str,
	uuid: str,
	*,
	prefer_bundle: bool = True,
	allow_raw_fallback: bool = True,
	timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
	"""Pull one trained model's file from Volume Vision.

	Returns `content` (the bytes), `url` (what was actually fetched),
	`endpoint` ("bundle" or "download"), `file_name`, `fell_back` (the /bundle
	endpoint was not there) and `warnings`. Raises ToolError for everything a
	caller can act on: an unreachable server, a uuid that names nothing, a body
	past the ceiling.

	`prefer_bundle=False` fetches the raw download and does not try /bundle at
	all — for an operator who knows their Volume Vision has not been upgraded
	yet and does not want the extra round trip in the log.
	"""
	timeout = max(1, int(timeout or DEFAULT_TIMEOUT_SECONDS))
	warnings: list = []

	if prefer_bundle:
		url = bundle_url(base_url, uuid)
		response = _get(url, timeout)
		status = int(getattr(response, "status_code", 0) or 0)
		if status == 200:
			return {
				"content": _body(response, url),
				"url": url,
				"endpoint": "bundle",
				"file_name": _file_name(response, url, uuid, ".bundle.zip"),
				"fell_back": False,
				"warnings": warnings,
				"server_version": (getattr(response, "headers", {}) or {}).get(HEADER_VERSION),
				"server_class_names": (getattr(response, "headers", {}) or {}).get(HEADER_CLASS_NAMES),
			}
		if status not in ENDPOINT_ABSENT_STATUSES:
			raise ToolError(
				f"{url} answered {status}. That is Volume Vision refusing this request rather than "
				"missing the endpoint, so nothing was fetched and nothing fell back — check that "
				f"{uuid} is a model on that server. Nothing was attached."
			)
		if not allow_raw_fallback:
			raise ToolError(
				f"{url} answered {status}, which means this Volume Vision does not serve bundles yet "
				"(Phase 1 of the model bundle pipeline is not deployed on it). "
				"allow_raw_fallback=true pulls the raw model from /download instead — with no "
				"manifest, so class_names stay whatever this record already says. Nothing was attached."
			)
		warnings.append(
			f"{url} answered {status}: this Volume Vision does not serve bundles yet. Fell back to "
			"the raw /download endpoint. THE FILE ATTACHED HAS NO MANIFEST — class_names on this "
			"record are whatever was entered here, not what training produced, and iOS will read "
			"them from the record rather than from the bundle. Re-run this tool once Phase 1 is "
			"deployed on the training server."
		)

	url = download_url(base_url, uuid)
	response = _get(url, timeout)
	status = int(getattr(response, "status_code", 0) or 0)
	if status != 200:
		raise ToolError(
			f"{url} answered {status}. That is the endpoint every existing consumer downloads "
			f"through, so a failure here means {uuid} does not name a model on that server, or the "
			"server is not the one that trained it. Nothing was attached."
		)
	return {
		"content": _body(response, url),
		"url": url,
		"endpoint": "download",
		"file_name": _file_name(response, url, uuid, ".mlmodel"),
		"fell_back": prefer_bundle,
		"warnings": warnings,
		"server_version": (getattr(response, "headers", {}) or {}).get(HEADER_VERSION),
		"server_class_names": (getattr(response, "headers", {}) or {}).get(HEADER_CLASS_NAMES),
	}
