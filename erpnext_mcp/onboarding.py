# SPDX-License-Identifier: MIT
"""Client-connection helpers for the settings form.

The last mile of installing this app used to be an operator hand-editing
`claude_desktop_config.json` from a README, with a token they had one chance to
copy. That is the step people get wrong — a stray comma, the wrong path, the
config overwritten rather than merged — and every one of those failures looks
identical from the client: "no tools". So the site generates the config itself.

THE TOKEN LEAVES THE SERVER EXACTLY TWICE. Once at generation, in the dialog, and
once here — to a System Manager who is already looking at the form that could
regenerate it. Both are gated on the same role, which is the honest boundary:
somebody who can read this panel could press **Generate New Token** and read the
result anyway.

Everything else about it is belt: the preview renders masked by default, so the
panel is safe on a shared screen or a screenshot, and the copy and download paths
fetch the real value separately. The token is never put in a URL — the download
is a GET whose *response* carries it — so it does not land in a proxy log or a
browser history entry.

WHY MODULE-LEVEL RATHER THAN CONTROLLER METHODS. The download has to be a plain
`GET` the browser can open in a new tab, and a whitelisted method on a DocType
controller is only reachable through `run_doc_method` with a POST. Keeping both
functions here gives the download a short, stable URL
(`/api/method/erpnext_mcp.onboarding.download_claude_desktop_config`) and keeps
the preview beside it.
"""

import ipaddress
import json
from urllib.parse import urlsplit

import frappe

from . import settings

SERVER_KEY = "erpnext"
ENDPOINT_PATH = "/api/method/erpnext_mcp.mcp.handle"
DOWNLOAD_FILENAME = "claude_desktop_config.json"

#: Where Claude Desktop keeps its config, per platform. These are the defaults;
#: a portable install or a managed deployment can differ, which is why the panel
#: says "default location" rather than "the location".
CONFIG_PATHS = {
	"macos": "~/Library/Application Support/Claude/claude_desktop_config.json",
	"windows": "%APPDATA%\\Claude\\claude_desktop_config.json",
	"linux": "~/.config/Claude/claude_desktop_config.json",
}

OS_LABELS = {"macos": "macOS", "windows": "Windows", "linux": "Linux"}

QUIT_KEYS = {
	"macos": "⌘Q",
	"windows": "Alt+F4",
	"linux": "Ctrl+Q",
}


# ── pure helpers (no Frappe state; unit-testable without a bench) ────────────
def mask(token: str) -> str:
	"""`••••••••…wxyz` — enough to tell two tokens apart, not enough to use one."""
	token = token or ""
	if not token:
		return ""
	if len(token) <= 4:
		return "•" * len(token)
	return "•" * 8 + "…" + token[-4:]


def detect_os(user_agent: str) -> str:
	"""Best guess at the operator's platform from a User-Agent string.

	A guess, and labelled as one in the UI: the browser showing this form is not
	necessarily the machine Claude Desktop runs on, so all three paths are always
	rendered and this only decides which is highlighted.
	"""
	agent = (user_agent or "").lower()
	if "windows" in agent:
		return "windows"
	if "mac os" in agent or "macintosh" in agent:
		return "macos"
	if "linux" in agent or "x11" in agent:
		return "linux"
	return "macos"


def build_config(url: str, token: str) -> dict:
	"""The `mcpServers` entry to paste into `claude_desktop_config.json`.

	`--allow-http` is included only for an `http://` endpoint. `mcp-remote`
	refuses a non-HTTPS origin without it, and adding it to an HTTPS config is
	noise that invites the question "why is this allowing http".
	"""
	args = ["-y", "mcp-remote", url, "--transport", "http-only"]
	if url.lower().startswith("http://"):
		args.append("--allow-http")
	args += ["--header", f"X-MCP-Token: {token}"]
	return {"mcpServers": {SERVER_KEY: {"command": "npx", "args": args}}}


def build_claude_code_command(url: str, token: str) -> str:
	"""The one-liner for Claude Code, which speaks HTTP MCP without a bridge."""
	return f'claude mcp add --transport http {SERVER_KEY} {url} --header "X-MCP-Token: {token}"'


# ── working out the URL a client should use ─────────────────────────────────
#
# v0.4.0 asked `frappe.utils.get_url()` and pasted the answer. On a bare-IP
# Umbrel that produced `http://100.69.162.122/api/...` — right host, no port —
# and a config that silently does nothing.
#
# THE PORT IS NOT BEING DROPPED. It never arrives. frappe_docker's nginx proxies
# with `proxy_set_header Host $host`, and nginx's `$host` is the *normalised*
# host: lowercased, port removed (`$http_host` is the raw one). So by the time
# Python sees the request, `frappe.local.request.host` is already `100.69.162.122`
# and `get_url()` has nothing to preserve.
#
# Worse, the port `get_url()` *would* append in that branch is
# `frappe.conf.http_port or webserver_port` — the container-internal 8000, not
# the published 5300. A published Docker port is a property of the compose file;
# nothing inside the container can see it.
#
# So the port has to come from something that was outside. The browser rendering
# this form reached the site at the very address the operator will paste into a
# client, and its `Origin` (or `Referer`) header carries that address with the
# port intact. That is the strongest signal available, and it is the one v0.4.0
# ignored.
#
# Candidates, best first. Each is labelled so the panel can show its work.

FORWARDED_HOST_HEADERS = ("X-Forwarded-Host", "X-Original-Host")


def _split(url: str):
	"""(scheme, host_with_port) from a URL-ish string, or (None, None)."""
	value = (url or "").strip().rstrip("/")
	if not value:
		return None, None
	if "//" not in value:
		value = "http://" + value
	parts = urlsplit(value)
	if not parts.netloc:
		return None, None
	return (parts.scheme or "http"), parts.netloc


def _hostname(netloc: str) -> str:
	return (netloc or "").rsplit("@", 1)[-1].rsplit(":", 1)[0].strip("[]").lower()


def _port(netloc: str) -> str:
	tail = (netloc or "").rsplit("@", 1)[-1]
	if tail.startswith("["):  # IPv6 literal
		tail = tail.split("]", 1)[-1]
	return tail.rsplit(":", 1)[1] if ":" in tail else ""


def is_bare_ip(url: str) -> bool:
	"""Whether the URL's host is a literal address rather than a name."""
	_scheme, netloc = _split(url)
	try:
		ipaddress.ip_address(_hostname(netloc))
	except ValueError:
		return False
	return True


def borrow_port(base: str, donor: str) -> str:
	"""Give `base` the donor's port, when it has none and they name the same host.

	The narrow case this exists for: a deployment sets `host_name` to
	`umbrel.local` while the operator browses `umbrel.local:5300`. The configured
	name is the right host and the browser knows the right port; neither alone is
	a working URL. Hostnames must match, so a `host_name` pointing somewhere else
	entirely is never given a port that does not belong to it.
	"""
	scheme, netloc = _split(base)
	_donor_scheme, donor_netloc = _split(donor)
	if not netloc or not donor_netloc:
		return base
	if _port(netloc) or not _port(donor_netloc):
		return base
	if _hostname(netloc) != _hostname(donor_netloc):
		return base
	return f"{scheme}://{netloc}:{_port(donor_netloc)}"


def browser_origin() -> str:
	"""Where the browser thinks it is — scheme, host and port, as typed.

	`Origin` on the form's POST; `Referer` for the download, which is a plain GET
	from a link and carries no Origin.
	"""
	origin = (frappe.get_request_header("Origin") or "").strip()
	scheme, netloc = _split(origin)
	if netloc:
		return f"{scheme}://{netloc}"
	scheme, netloc = _split(frappe.get_request_header("Referer") or "")
	return f"{scheme}://{netloc}" if netloc else ""


def configured_host_name() -> str:
	"""`host_name` from site_config / common_site_config, if the operator set one.

	This is the canonical name Frappe itself prefers inside `get_url()`, and on a
	multi-site bench it is the name that actually routes — which is why it beats
	whatever host the browser happened to use.
	"""
	conf = getattr(frappe, "conf", None) or {}
	name = (conf.get("host_name") or conf.get("hostname") or "").strip()
	scheme, netloc = _split(name)
	return f"{scheme}://{netloc}" if netloc else ""


def forwarded_base() -> str:
	"""Reconstructed from proxy headers, for a proxy that sets them properly."""
	host = ""
	for header in FORWARDED_HOST_HEADERS:
		host = (frappe.get_request_header(header) or "").split(",")[0].strip()
		if host:
			break
	if not host:
		return ""
	scheme = (frappe.get_request_header("X-Forwarded-Proto") or "").split(",")[0].strip() or "http"
	port = (frappe.get_request_header("X-Forwarded-Port") or "").split(",")[0].strip()
	if port and ":" not in host:
		host = f"{host}:{port}"
	return f"{scheme}://{host}"


def request_base() -> str:
	"""The socket-level view. Portless behind nginx's `$host` — see the note above."""
	request = getattr(frappe.local, "request", None)
	host = (getattr(request, "host", "") or "").strip()
	if not host:
		return ""
	scheme = (frappe.get_request_header("X-Forwarded-Proto") or "").split(",")[0].strip()
	if not scheme:
		scheme = getattr(request, "scheme", "") or "http"
	return f"{scheme}://{host}"


def url_candidates() -> list:
	"""Every base considered, best first, each with where it came from.

	Surfaced in the payload so an operator staring at a URL they did not expect
	can see what was available and why this one won, rather than guessing.
	"""
	origin = browser_origin()
	raw = [
		("public_url", settings.public_url()),
		("host_name (site config)", configured_host_name()),
		("browser Origin/Referer", origin),
		("X-Forwarded-* headers", forwarded_base()),
		("request Host", request_base()),
		("frappe.utils.get_url()", _safe_get_url()),
	]
	out = []
	for source, base in raw:
		base = (base or "").strip().rstrip("/")
		if not base:
			continue
		# The configured name is the right host but often has no port; the browser
		# knows the port. Neither is a working URL alone.
		if source == "host_name (site config)" and origin:
			base = borrow_port(base, origin)
		out.append({"source": source, "base": base})
	return out


def _safe_get_url() -> str:
	try:
		return frappe.utils.get_url() or ""
	except Exception:
		return ""


def resolve_base() -> tuple:
	"""(base, source, candidates) — the winner and the reasoning behind it."""
	candidates = url_candidates()
	if not candidates:
		return "", "none", []
	return candidates[0]["base"], candidates[0]["source"], candidates


def endpoint_url() -> str:
	"""The URL a client should POST to."""
	base, _source, _candidates = resolve_base()
	return f"{base.rstrip('/')}{ENDPOINT_PATH}"


def routing_warning(url: str) -> dict:
	"""Whether this URL is likely to be refused by Frappe's site routing.

	Frappe picks a site from the request: the `X-Frappe-Site-Name` header if a
	proxy sets one, otherwise the Host, otherwise `default_site` from
	common_site_config.json. A bare IP matches no site directory, so unless one of
	the other two is in play the client gets a "site not found" page and no
	explanation of why the browser works and the config does not.

	Returns `{}` when there is nothing to say — the panel renders a banner only
	for a truthy value.
	"""
	if not is_bare_ip(url):
		return {}
	conf = getattr(frappe, "conf", None) or {}
	if (conf.get("default_site") or "").strip():
		return {}
	if (frappe.get_request_header("X-Frappe-Site-Name") or "").strip():
		# A proxy is pinning the site for every request, so the Host is not used
		# for routing and a bare IP is harmless.
		return {}
	return {
		"code": "BARE_IP_NO_DEFAULT_SITE",
		"message": (
			"This URL uses a bare IP address. Frappe routes a request to a site by "
			"its Host header, and an IP matches no site — so a client using this "
			"URL may get a “site not found” error even though this browser works. "
			"Fix it in one of three ways: set default_site in "
			"common_site_config.json, set host_name to a name that resolves for "
			"your clients, or put that name in Public URL above."
		),
		"host": _hostname(_split(url)[1]),
	}


@frappe.whitelist()
def claude_desktop_config(reveal=0) -> dict:
	"""Everything the settings panel renders. System Manager only.

	`reveal` decides whether the token appears in the payload or a mask does.
	Default masked: the panel is often open on a shared screen, and the copy and
	download paths ask for the real value separately.
	"""
	frappe.only_for("System Manager")

	# settings.auth_token() resolves to Frappe's
	# `frappe.utils.password.get_decrypted_password` under the Password field,
	# and already fails closed on a site whose encryption key has been rotated.
	token = settings.auth_token()
	base, source, candidates = resolve_base()
	url = f"{base.rstrip('/')}{ENDPOINT_PATH}"
	shown = token if frappe.utils.cint(reveal) and token else mask(token)
	detected = detect_os(frappe.get_request_header("User-Agent") or "")

	return {
		"ready": bool(settings.is_enabled() and token),
		"enabled": settings.is_enabled(),
		"token_configured": bool(token),
		"revealed": bool(frappe.utils.cint(reveal) and token),
		"endpoint_url": url,
		"url_source": source,
		"url_candidates": candidates,
		"routing_warning": routing_warning(url),
		"is_http": url.lower().startswith("http://"),
		"detected_os": detected,
		"config_paths": CONFIG_PATHS,
		"os_labels": OS_LABELS,
		"quit_keys": QUIT_KEYS,
		"config": build_config(url, shown),
		"config_json": json.dumps(build_config(url, shown), indent=2),
		"claude_code_command": build_claude_code_command(url, shown),
		"download_url": "/api/method/erpnext_mcp.onboarding.download_claude_desktop_config",
	}


@frappe.whitelist(methods=["GET"])
def download_claude_desktop_config():
	"""Serve the config as a `.json` attachment. System Manager only.

	GET so the browser can open it directly; the token rides in the response
	body, never in the URL, so it does not reach a proxy log or a history entry.
	"""
	frappe.only_for("System Manager")
	token = settings.auth_token()
	if not token:
		frappe.throw(
			frappe._("Generate an auth token before downloading a client config."),
			title=frappe._("No Token"),
		)

	payload = json.dumps(build_config(endpoint_url(), token), indent=2) + "\n"
	frappe.response["type"] = "binary"
	frappe.response["filename"] = DOWNLOAD_FILENAME
	frappe.response["filecontent"] = payload.encode("utf-8")
