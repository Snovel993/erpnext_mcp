# SPDX-License-Identifier: MIT
"""What a tool handler hands back to the dispatcher."""

from dataclasses import dataclass, field


@dataclass
class ToolResult:
	"""A tool's payload plus the two things the audit log needs.

	`data` is serialised to JSON and returned to the client. `summary` is the
	one-line human description that lands in MCP Action Log — write it so an
	operator scanning the log a month later can tell what happened without
	unpacking `arguments_json`. `docstatus_delta` is set only by mutating tools
	and reads like `"none → 0 (draft)"` or `"0 → 1 (submitted)"`.
	"""

	data: dict = field(default_factory=dict)
	summary: str = ""
	docstatus_delta: str = ""
