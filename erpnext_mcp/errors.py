# SPDX-License-Identifier: MIT
"""The one exception type a tool raises for an expected failure.

A `ToolError` means "you asked for something I can't do, and that is not a
bug": an account name that resolves to nothing, a kill switch that is off, a
Journal Entry whose debits don't equal its credits. It becomes an MCP tool
error (`isError: true`) with the message intact so the model can correct itself
and retry — never an HTTP 500, and never a Frappe traceback in the response.

Anything that is NOT a ToolError is a bug in this app or in the site, and is
reported to the client as `<ExceptionType>: <message>` with the full traceback
written to the site's Error Log instead.
"""


class ToolError(Exception):
	"""An expected, client-correctable tool failure."""


class AuthError(Exception):
	"""The caller failed a transport-level gate (token, CIDR, master switch).

	Deliberately distinct from ToolError: an AuthError never reaches the tool
	layer, is never given a descriptive reason (see `security.py` on why), and
	is answered with an HTTP status rather than a tool result.
	"""

	def __init__(self, message: str, http_status: int = 401, log_reason: str = ""):
		super().__init__(message)
		self.http_status = http_status
		# What we write to the audit log — as specific as we like, because it
		# never leaves the server.
		self.log_reason = log_reason or message
