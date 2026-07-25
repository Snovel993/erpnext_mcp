# SPDX-License-Identifier: MIT
"""The two MCP tools over the compliance-packet framework.

WHY TWO TOOLS RATHER THAN ONE PER PACKET TYPE. Packet types are site-dependent
and will multiply — payroll needs Frappe HR, an organic-transition attestation
needs farm data, a SOX packet only makes sense for some entities. Registering
each as its own MCP tool would mean a JSON schema, a registry entry and a
settings field per type, and a model would still have no way to discover which
of them this particular site can produce. So: `list_compliance_packets` is the
discovery call and `generate_compliance_packet` is the doing call, exactly as
`get_company_topology` is to the ledger tools.

Each packet type keeps its own `allow_<packet_type>` switch, so enabling the
tool does not enable every packet. Two gates, deliberately: the tool switch says
"this site produces compliance artefacts at all", the packet switch says "…and
this one".
"""

from .. import packets, settings
from ..errors import ToolError
from ..result import ToolResult


# ── 36. list_compliance_packets ─────────────────────────────────────────────
def list_compliance_packets(args: dict) -> ToolResult:
	"""Which packet types this site can produce, and what each one takes."""
	available, unavailable, disabled = [], [], []
	for packet_type in packets.names():
		spec = packets.PACKETS[packet_type]
		if not spec.is_available():
			unavailable.append(
				{
					"packet_type": packet_type,
					"title": spec.title,
					"requires": spec.requires or "a component this site does not have",
				}
			)
		elif not settings.tool_enabled(packet_type):
			disabled.append(
				{
					"packet_type": packet_type,
					"title": spec.title,
					"switch": f"allow_{packet_type}",
				}
			)
		else:
			available.append(spec.describe())

	data = {
		"packets": available,
		"count": len(available),
		"disabled": disabled,
		"unavailable": unavailable,
		"note": (
			"Call generate_compliance_packet with a packet_type from `packets` and "
			"a `filters` object matching its `filters` schema. Entries under "
			"`disabled` need an operator to tick their switch; entries under "
			"`unavailable` cannot run on this site at all."
		),
	}
	return ToolResult(data, f"{len(available)} packet type(s) available")


# ── 37. generate_compliance_packet ──────────────────────────────────────────
def generate_compliance_packet(args: dict) -> ToolResult:
	"""Build one compliance packet and return it inline.

	Read-only: no document is created, nothing is stored, nothing is emailed. The
	packet is a value returned to the caller, which is why it carries its own
	provenance — it has no filename or docname to be identified by.
	"""
	packet_type = str(args.get("packet_type") or "").strip()
	if not packet_type:
		raise ToolError("packet_type is required. Call list_compliance_packets to see what this site offers.")

	spec = packets.get(packet_type)
	if spec is None:
		raise ToolError(
			f"no packet type {packet_type!r}. This site knows: {', '.join(packets.names()) or '<none>'}."
		)
	if not spec.is_available():
		raise ToolError(
			f"the packet type {packet_type!r} is not available on this site: it "
			f"requires {spec.requires or 'a component this site does not have'}. "
			"This is not something an operator can switch on here."
		)
	if not settings.tool_enabled(packet_type):
		raise ToolError(
			f"the packet type {packet_type!r} is switched off on this site. An "
			f"operator must tick 'allow_{packet_type}' in ERPNext MCP Settings to "
			"enable it."
		)

	filters = packets.validate_filters(spec, args.get("filters"))
	result = spec.build(filters)
	data = packets.envelope(spec, filters, result)

	worst = data["flag_summary"]["worst"]
	summary = result.summary or f"generated {packet_type}"
	if worst:
		summary = f"{summary} [worst flag: {worst}]"
	return ToolResult(data, summary)


def packet_switch_names() -> tuple:
	"""Every `allow_<packet_type>` field the settings doctype must carry.

	Used by the settings invariant tests, which check that a packet type cannot
	ship without a way to turn it off.
	"""
	return tuple(f"allow_{name}" for name in packets.names())
