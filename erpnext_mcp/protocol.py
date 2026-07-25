# SPDX-License-Identifier: MIT
"""JSON-RPC 2.0, the wire format MCP speaks. No HTTP in this file.

WHY THERE IS NO MCP SDK HERE. The official `mcp` package ships an asyncio server
runtime. Frappe is synchronous WSGI: a request is a thread, and there is no
event loop to hand a long-lived server object to. Bolting one in means running
an asyncio loop inside a Werkzeug worker for the duration of a request, which is
a lot of machinery to end up with the same three JSON-RPC methods. So this
module implements `initialize`, `tools/list` and `tools/call` directly — which
is precisely the request/response surface of MCP's Streamable HTTP transport,
so a standard client talks to it without knowing the difference.

VERSION NEGOTIATION. MCP's `initialize` carries the client's `protocolVersion`.
The spec's rule is to echo it back when the server supports it and to answer
with the server's own preferred version when it does not, leaving the client to
decide whether to continue. That is what `_initialize` does, rather than
hardcoding one version and hoping.

NOTIFICATIONS GET NO REPLY. A JSON-RPC message with no `id` is a notification;
returning a response to one is a protocol violation that some clients treat as
a fatal error. `handle_message` returns None for those and the transport turns
that into an empty 202.
"""

from . import __version__, registry

#: Newest first. `initialize` echoes the client's version when it is in here.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

#: What we answer with when the client asks for something we do not know.
PREFERRED_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

SERVER_NAME = "erpnext-mcp"

# JSON-RPC 2.0 reserved codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def handle_payload(payload, caller_ip: str = ""):
	"""Handle a parsed request body — one message or a batch.

	Returns `(body, status)`. `body` is None for a payload that was entirely
	notifications, which the transport answers with 202 and no content.
	"""
	if isinstance(payload, list):
		if not payload:
			return rpc_error(None, INVALID_REQUEST, "empty batch"), 400
		responses = [
			response
			for response in (handle_message(message, caller_ip) for message in payload)
			if response is not None
		]
		return (responses or None), 200
	if not isinstance(payload, dict):
		return rpc_error(None, INVALID_REQUEST, "request must be an object or an array"), 400
	response = handle_message(payload, caller_ip)
	return response, 200


def handle_message(message: dict, caller_ip: str = ""):
	"""Handle one JSON-RPC message. None means "notification, no reply"."""
	if not isinstance(message, dict):
		return rpc_error(None, INVALID_REQUEST, "message must be an object")

	request_id = message.get("id")
	method = message.get("method")
	params = message.get("params") or {}
	if not isinstance(params, dict):
		return rpc_error(request_id, INVALID_PARAMS, "params must be an object")

	if not method:
		return rpc_error(request_id, INVALID_REQUEST, "missing method")

	if method == "initialize":
		return rpc_result(request_id, _initialize(params))

	# Notifications. `notifications/initialized` is the handshake's third leg;
	# the cancelled/progress ones exist so a client that sends them does not get
	# a "method not found" it will log as an error.
	if method.startswith("notifications/") or method == "initialized":
		return None

	if method == "ping":
		return rpc_result(request_id, {})

	if method == "tools/list":
		return rpc_result(request_id, registry.tools_list())

	if method == "tools/call":
		name = params.get("name")
		if not name or not isinstance(name, str):
			return rpc_error(request_id, INVALID_PARAMS, "params.name (tool name) is required")
		arguments = params.get("arguments") or {}
		if not isinstance(arguments, dict):
			return rpc_error(request_id, INVALID_PARAMS, "params.arguments must be an object")
		# A tool failure is a *result* with isError set, not a JSON-RPC error:
		# JSON-RPC errors mean the call itself was malformed, and a model needs
		# to see the difference to know whether retrying differently can help.
		return rpc_result(request_id, registry.dispatch(name, arguments, caller_ip))

	# Capabilities this server does not advertise. Answering "method not found"
	# is correct and is what the spec expects a tools-only server to do.
	if method in (
		"resources/list",
		"resources/read",
		"resources/templates/list",
		"prompts/list",
		"prompts/get",
		"completion/complete",
		"logging/setLevel",
	):
		return rpc_error(
			request_id,
			METHOD_NOT_FOUND,
			f"{method} is not supported: this server advertises tools only",
		)

	if request_id is None:
		return None
	return rpc_error(request_id, METHOD_NOT_FOUND, f"method not found: {method}")


def _initialize(params: dict) -> dict:
	requested = str(params.get("protocolVersion") or "").strip()
	negotiated = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else PREFERRED_PROTOCOL_VERSION
	return {
		"protocolVersion": negotiated,
		"capabilities": {"tools": {"listChanged": False}},
		"serverInfo": {
			"name": SERVER_NAME,
			"version": __version__,
			"title": "ERPNext MCP",
		},
		"instructions": (
			"This server exposes one ERPNext site. Call get_company_topology "
			"first: company, account and fiscal-year names are specific to this "
			"install and every other tool needs them. Use search_accounts to turn "
			"an account description into a docname. Tools that change data are off "
			"unless an operator has enabled them individually, and "
			"create_journal_entry only ever produces a draft — posting is a "
			"separate, separately-enabled tool."
		),
	}


def rpc_result(request_id, result) -> dict:
	return {"jsonrpc": "2.0", "id": request_id, "result": result}


def rpc_error(request_id, code: int, message: str) -> dict:
	return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


__all__ = [
	"INVALID_PARAMS",
	"INVALID_REQUEST",
	"METHOD_NOT_FOUND",
	"PARSE_ERROR",
	"PREFERRED_PROTOCOL_VERSION",
	"SUPPORTED_PROTOCOL_VERSIONS",
	"handle_message",
	"handle_payload",
	"rpc_error",
	"rpc_result",
]
