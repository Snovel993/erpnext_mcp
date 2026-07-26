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

import json

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


# ── site-aware ───────────────────────────────────────────────────────────────
def endpoint_url() -> str:
	"""The URL a client should POST to.

	`public_url` on the settings doctype wins when set. A site behind a Tailscale
	Funnel, an ngrok tunnel or a reverse proxy on another hostname has a
	`frappe.utils.get_url()` that is correct for the server and useless to the
	client, and there is no way to detect that from inside the request — so it is
	a field an operator fills in rather than something guessed.
	"""
	base = (settings.public_url() or "").strip()
	if not base:
		base = frappe.utils.get_url()
	return f"{base.rstrip('/')}{ENDPOINT_PATH}"


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
	url = endpoint_url()
	shown = token if frappe.utils.cint(reveal) and token else mask(token)
	detected = detect_os(frappe.get_request_header("User-Agent") or "")

	return {
		"ready": bool(settings.is_enabled() and token),
		"enabled": settings.is_enabled(),
		"token_configured": bool(token),
		"revealed": bool(frappe.utils.cint(reveal) and token),
		"endpoint_url": url,
		"url_source": "public_url" if settings.public_url() else "frappe.utils.get_url()",
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
