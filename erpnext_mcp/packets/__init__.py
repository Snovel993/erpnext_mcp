# SPDX-License-Identifier: MIT
"""Compliance packets: structured artefacts an accountant or auditor signs off.

Every module in this package that registers a `PacketSpec` becomes a packet type
automatically — discovery walks the package at import time, so adding one is a
single file drop with no list to update and no handler to edit. That indirection
exists because the roadmap for this family is long (payroll, organic-transition
attestations, tax-year summaries, SOX controls) and each of those will be
written by somebody who should not have to understand the dispatcher.

A packet type is not a tool. Tools are the MCP surface —
`generate_compliance_packet` and `list_compliance_packets` — and packet types
are what they operate on, each with its own `allow_<packet_type>` switch so an
operator can expose the reconciliation packet without exposing a payroll one.
"""

import importlib
import pkgutil

from .base import (  # noqa: F401  (re-exported for packet modules and tools)
	PACKETS,
	ROW_CAP,
	SEVERITY_ERROR,
	SEVERITY_INFO,
	SEVERITY_ORDER,
	SEVERITY_WARN,
	TOLERANCE,
	Flag,
	PacketResult,
	PacketSpec,
	cap,
	envelope,
	money,
	register,
	validate_filters,
)


def _discover() -> None:
	"""Import every sibling module so its `register(...)` call runs.

	Failures are deliberately not swallowed. A packet type that cannot import is
	a packet type that would silently vanish from the catalogue, and a compliance
	artefact quietly not being offered is exactly the kind of absence nobody
	notices until an audit.
	"""
	for module in pkgutil.iter_modules(__path__):
		if module.name.startswith("_") or module.name == "base":
			continue
		importlib.import_module(f"{__name__}.{module.name}")


_discover()


def get(packet_type: str):
	"""A registered PacketSpec, or None."""
	return PACKETS.get(packet_type)


def names() -> tuple:
	"""Every registered packet type, in a stable order."""
	return tuple(sorted(PACKETS))
