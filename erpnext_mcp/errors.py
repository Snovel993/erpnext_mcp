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
	"""An expected, client-correctable tool failure.

	`key` IS OPTIONAL AND THE MESSAGE IS NOT. v0.85.0 lets a refusal carry a
	stable translation key — `error.shift.already_open` — beside the English
	sentence, so the mobile surface can serve a Spanish-speaking picker a refusal
	they can read. The English stays because it is what a model corrects itself
	from and what an operator finds in a log; the key is an addition to the
	message, never a replacement for it.

	A raise with no key is not a defect and never becomes one. Several hundred
	call sites in this app raise a `ToolError` with one positional argument, and
	the refusals a WORKER ON A PHONE can actually hit are a small subset of them:
	an accounting tool refusing an unbalanced Journal Entry is read by a model,
	not by somebody standing in a block. `api/guard` falls back to
	`error.unspecified` and passes the English through unchanged, which is
	exactly what those call sites did before this release.
	"""

	def __init__(self, message: str, key: str = "", **fill):
		super().__init__(message)
		#: A `Farm Translation` key, or "". See `tools/translations.py`.
		self.translation_key = str(key or "").strip()
		#: Values for the `{placeholders}` in the translated string. The English
		#: message above is already rendered; these are for the OTHER language,
		#: whose sentence puts the same values in a different order.
		self.translation_fill = dict(fill)


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
