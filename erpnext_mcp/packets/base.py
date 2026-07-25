# SPDX-License-Identifier: MIT
"""The compliance-packet framework: what a packet is, and how one is registered.

A PACKET IS AN ARTEFACT, NOT AN ANSWER. The read tools answer questions; a
packet is the thing you hand to somebody who has to sign off. That difference
drives every design decision here:

  * **It says how it was made.** Every packet carries `generated_at`,
    `generated_by`, `site`, `generator_version` and `mcp_action_log_id` — the
    MCP Action Log row for the call that produced it. An accountant looking at a
    figure six months later can trace it back to a request, an IP and a user.
  * **It never truncates quietly.** A tool that silently returns the first 100
    rows is fine. A reconciliation packet that silently omits entries is a lie
    with provenance attached, which is worse than no packet. Every collection is
    capped at `ROW_CAP` and any cap that bites raises a WARN flag naming the
    number omitted.
  * **It reports what is wrong with itself.** `flags` is not decoration. An
    auditor's first question is "what should I look at", and a packet that
    computes its own anomalies — cancelled entries, arithmetic that does not
    close, drafts that would move a balance — answers it before it is asked.

SEVERITY MEANS SOMETHING. INFO is context. WARN is "a human should look".
ERROR is "these numbers do not internally agree" — an arithmetic or integrity
failure inside the packet itself, not merely an unusual business fact. A packet
with an ERROR flag should not be signed.

ADDING A PACKET TYPE is one file in this directory that ends with
`register(PacketSpec(...))`. The package auto-discovers every module here at
import time, so there is no list to update and no handler to touch — the whole
reason for the indirection.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import frappe

from .. import __version__
from ..errors import ToolError

SEVERITY_INFO = "INFO"
SEVERITY_WARN = "WARN"
SEVERITY_ERROR = "ERROR"

#: Severity order, worst first — used to sort flags and pick the headline.
SEVERITY_ORDER = (SEVERITY_ERROR, SEVERITY_WARN, SEVERITY_INFO)

#: Hard cap on any single collection inside a packet. A packet is meant to be
#: read, and a JSON document with ten thousand journal entries in it is not.
#: Exceeding it is reported, never silent.
ROW_CAP = 1000

#: Currency comparisons. Anything under half a cent is float noise, not a
#: discrepancy; anything over it is real and gets a flag.
TOLERANCE = 0.005


@dataclass
class Flag:
	"""One thing about this packet a human should know."""

	code: str
	severity: str
	description: str
	detail: dict = field(default_factory=dict)

	def as_dict(self) -> dict:
		return {
			"code": self.code,
			"severity": self.severity,
			"description": self.description,
			"detail": self.detail,
		}


@dataclass
class PacketResult:
	"""What a packet builder returns: its body, its flags, and a one-liner."""

	data: dict = field(default_factory=dict)
	flags: list = field(default_factory=list)
	summary: str = ""


@dataclass
class PacketSpec:
	"""A registered packet type.

	`filters` is a JSON-Schema `properties` object describing what the packet
	takes, and is surfaced verbatim by `list_compliance_packets` — so a client
	learns how to call a packet type this app's own schema knows nothing about.
	`available` / `requires` work exactly as they do for tools: a packet type
	whose site prerequisite is unmet is not listed and cannot be generated.
	"""

	packet_type: str
	title: str
	purpose: str
	audience: str
	build: object
	filters: dict = field(default_factory=dict)
	required: tuple = ()
	available: object = None
	requires: str = ""

	def is_available(self) -> bool:
		if self.available is None:
			return True
		try:
			return bool(self.available())
		except Exception:
			return False

	def describe(self) -> dict:
		return {
			"packet_type": self.packet_type,
			"title": self.title,
			"purpose": self.purpose,
			"audience": self.audience,
			"filters": self.filters,
			"required_filters": list(self.required),
			"switch": f"allow_{self.packet_type}",
		}


#: packet_type → PacketSpec. Populated by `register` at import time.
PACKETS: dict = {}


def register(spec: PacketSpec) -> PacketSpec:
	"""Add a packet type to the registry. Called at module import."""
	if spec.packet_type in PACKETS:
		raise RuntimeError(f"duplicate packet type {spec.packet_type!r}")
	PACKETS[spec.packet_type] = spec
	return spec


# ── argument handling ───────────────────────────────────────────────────────
def validate_filters(spec: PacketSpec, filters: dict) -> dict:
	"""Check `filters` against the spec, with errors a model can act on.

	Unknown keys are rejected by name rather than ignored. A packet is evidence:
	if a caller thought it was scoping to one cost centre and the key was
	misspelt, silently generating the unscoped packet is the worst outcome.
	"""
	if filters is None:
		filters = {}
	if not isinstance(filters, dict):
		raise ToolError(f"filters must be an object, got {type(filters).__name__}")

	unknown = sorted(set(filters) - set(spec.filters))
	if unknown:
		raise ToolError(
			f"{spec.packet_type} does not take filter(s): {', '.join(unknown)}. "
			f"Accepted: {', '.join(sorted(spec.filters)) or '<none>'}."
		)
	missing = [key for key in spec.required if not str(filters.get(key) or "").strip()]
	if missing:
		raise ToolError(
			f"{spec.packet_type} requires filter(s): {', '.join(missing)}. "
			f"See list_compliance_packets for the full shape."
		)
	return filters


# ── assembly ────────────────────────────────────────────────────────────────
def envelope(spec: PacketSpec, filters: dict, result: PacketResult) -> dict:
	"""Wrap a builder's output in the provenance every packet carries.

	`mcp_action_log_id` is deliberately left None here and stamped by
	`registry.dispatch` once the audit row exists — a packet cannot know its own
	log id before the call it describes has been recorded. The key is present
	either way so a consumer never has to ask whether the field exists.
	"""
	flags = sorted(
		(flag.as_dict() for flag in result.flags),
		key=lambda f: (SEVERITY_ORDER.index(f["severity"]), f["code"]),
	)
	counts = {severity: 0 for severity in SEVERITY_ORDER}
	for flag in flags:
		counts[flag["severity"]] += 1

	return {
		"packet_type": spec.packet_type,
		"title": spec.title,
		"purpose": spec.purpose,
		"audience": spec.audience,
		"filters": filters,
		**result.data,
		"flags": flags,
		"flag_summary": {
			**counts,
			"worst": next((f["severity"] for f in flags), None),
			"signable": not any(f["severity"] == SEVERITY_ERROR for f in flags),
		},
		"generated_at": frappe.utils.now(),
		"generated_by": frappe.session.user,
		"site": frappe.local.site,
		"generator": "erpnext_mcp",
		"generator_version": __version__,
		# Stamped by registry.dispatch. See the docstring above.
		"mcp_action_log_id": None,
	}


def cap(rows: list, flags: list, collection: str) -> list:
	"""Trim a collection to ROW_CAP, flagging loudly if that bites."""
	if len(rows) <= ROW_CAP:
		return rows
	flags.append(
		Flag(
			code="TRUNCATED",
			severity=SEVERITY_WARN,
			description=(
				f"{collection} was truncated to {ROW_CAP} of {len(rows)} rows. This "
				"packet is INCOMPLETE — narrow the period or the filters before "
				"relying on it."
			),
			detail={"collection": collection, "returned": ROW_CAP, "total": len(rows)},
		)
	)
	return rows[:ROW_CAP]


def money(value) -> float:
	try:
		return round(float(value or 0), 2)
	except (TypeError, ValueError):
		return 0.0
