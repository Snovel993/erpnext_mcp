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

v0.19.0 ADDED `regime` AS A TOP-LEVEL ARGUMENT RATHER THAN AS A FILTER ON EACH
PACKET TYPE, and the distinction is worth a sentence. Both packet types this app
ships today are accounting artefacts — a reconciliation and a fiscal-year audit —
and putting a worker-training key in each one's `filters` schema would be putting
a WPS question on a form about a bank account. It is an ANNEX: it changes nothing
the packet computes and staples the training evidence for one regime to the back
of it, which is exactly the request that produces it ("send the reconciliation,
and your WPS training records for the same year"). See `_training_annex`.
"""

import frappe

from .. import alerts, compat, packets, settings, training
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

	regime = str(args.get("regime") or "").strip()
	if regime and not training.canon(regime):
		raise ToolError(f"regime {regime!r} is not one this app knows. {training.vocabulary_note()}")

	filters = packets.validate_filters(spec, args.get("filters"))
	result = spec.build(filters)
	data = packets.envelope(spec, filters, result)
	if regime:
		canonical = training.canon(regime)
		data["training_evidence"] = _training_annex(canonical, filters)
		data["compliance_alerts"] = _alert_annex(canonical, filters)

	worst = data["flag_summary"]["worst"]
	summary = result.summary or f"generated {packet_type}"
	if worst:
		summary = f"{summary} [worst flag: {worst}]"
	return ToolResult(data, summary)


# ── the training annex ──────────────────────────────────────────────────────
def _training_annex(regime: str, filters: dict) -> dict:
	"""Every training record carrying `regime`, over whatever period the packet covers.

	v0.19.0. WHY A TOP-LEVEL ARGUMENT RATHER THAN A FILTER ON EACH PACKET TYPE.
	Both packet types this app ships today are accounting artefacts — a
	reconciliation and a fiscal-year audit — and adding `regime` to each one's
	`filters` schema would put a worker-training key on a form about a bank
	account. It is an ANNEX: it does not change what the packet computes, it
	staples the training evidence for one regime to the back of it, which is
	exactly the request that produces it ("send the reconciliation, and your WPS
	training records for the same year").

	THE PERIOD IS THE PACKET'S OWN. `period_start`/`period_end` where the filters
	carry them, the Fiscal Year's own dates where they carry a fiscal year, and
	ALL TIME where neither — stated in the result rather than guessed at silently,
	because "no training records" and "no period to look in" are different answers
	and only one of them is about the crew.
	"""
	company = str(filters.get("company") or "")
	start, end, basis = _period_of(filters)
	if not compat.doctype_exists(training.DOCTYPE):
		return {
			"regime": regime,
			"available": False,
			"records": [],
			"count": 0,
			"note": (
				"This site has no Employee Training Record DocType — it ships with erpnext_mcp "
				"from v0.19.0, so run `bench --site <site> migrate`. This annex is ABSENT rather "
				"than empty: an empty training table reads as a crew nobody trained."
			),
		}
	rows = [
		training.describe(row)
		for row in training.for_regime(regime, company, start, end, limit=packets.ROW_CAP + 1)
	]
	capped = rows[: packets.ROW_CAP]
	unreviewed = [row["name"] for row in capped if not row["supervisor_reviewed"]]
	out = {
		"regime": regime,
		"regime_note": training.REGIME_NOTES.get(regime),
		"available": True,
		"company": company or None,
		"period_start": start or None,
		"period_end": end or None,
		"period_basis": basis,
		"count": len(capped),
		"truncated": len(rows) > packets.ROW_CAP,
		"records": capped,
		"without_supervisor_review": unreviewed,
		"retention_years": training.RETENTION_YEARS.get(regime, 3),
		"retention_citation": training.RETENTION_CITATIONS.get(regime),
	}
	if unreviewed:
		out["review_note"] = (
			f"{len(unreviewed)} of these have no supervisor review. FSMA §112.161(b) requires "
			"worker training records to be reviewed, dated and signed by a supervisor within a "
			"reasonable time after the record is made."
		)
	if not capped:
		out["empty_note"] = (
			f"No training record tagged {regime} falls in this period. That is a statement about "
			"the period and the tag, not about whether anybody was trained — an untagged record "
			"exists but appears in no packet."
		)
	return out


def _alert_annex(regime: str, filters: dict) -> dict:
	"""What the compliance calendar still has open for `regime`, as a second annex.

	v0.19.2, AND IT IS THE SAME ARGUMENT AS `_training_annex` ONE STEP FURTHER. The
	request that produces a regime-scoped packet is "send the reconciliation, and
	your WPS records for the same year" — and the honest answer to the second half
	is not only what the crew was taught but what the operation currently knows it
	still owes. A packet that annexed the evidence and silently omitted the
	outstanding items would be curating rather than answering.

	OPEN MEANS OPEN. Dismissed items are excluded because a dismissal is somebody's
	recorded judgement that the item did not need doing, and snoozed ones because a
	snooze is "not this week" rather than an obligation. Both are still on the
	calendar and neither is hidden from anybody who reads it there; what they are
	not is a debt.

	NOT SCOPED TO THE PERIOD, unlike the training annex, and the asymmetry is
	deliberate. A training record is evidence ABOUT a period. An open alert is a
	fact about TODAY — an expired licence is expired now whatever the reconciliation
	covers — so filtering it by the packet's dates would produce a list of things
	that were outstanding last March, which is a different and much less useful
	document.
	"""
	company = str(filters.get("company") or "")
	if not compat.doctype_exists(alerts.ALERT_DOCTYPE):
		return {
			"regime": regime,
			"available": False,
			"alerts": [],
			"count": 0,
			"note": (
				"This site has no Compliance Alert DocType — it ships with erpnext_mcp, so run "
				"`bench --site <site> migrate`. This annex is ABSENT rather than empty: an empty "
				"list of open items reads as an operation that owes nothing."
			),
		}

	alert_filters = {"dismissed": 0}
	if company:
		alert_filters["company"] = company
	rows = [
		dict(row)
		for row in frappe.db.get_all(
			alerts.ALERT_DOCTYPE,
			filters=alert_filters,
			fields=compat.existing_fields(
				alerts.ALERT_DOCTYPE,
				(
					"name",
					"alert_type",
					"severity",
					"category",
					"company",
					"alert_message",
					"due_date",
					"first_seen",
					"snoozed_until",
				),
			),
			order_by="due_date asc",
			limit=packets.ROW_CAP * 2 + 1,
		)
		or []
	]
	tags = alerts.regimes_for_alerts([row.get("name") for row in rows])
	today = frappe.utils.today()
	found = []
	for row in rows:
		snoozed = str(row.get("snoozed_until") or "")
		if snoozed and snoozed >= today:
			continue
		# By TOKEN, never substring — 'GlobalGAP' contains 'GAP'.
		if not alerts.alert_matches_regime(tags.get(str(row.get("name")), []), regime):
			continue
		found.append(
			{
				"name": row.get("name"),
				"alert_type": row.get("alert_type"),
				"severity": row.get("severity"),
				"category": row.get("category"),
				"message": row.get("alert_message"),
				"due_date": str(row.get("due_date") or "") or None,
				"open_since": str(row.get("first_seen") or "") or None,
			}
		)

	capped = found[: packets.ROW_CAP]
	critical = [item["name"] for item in capped if item["severity"] == alerts.SEVERITY_CRITICAL]
	overdue = [item["name"] for item in capped if item["due_date"] and item["due_date"] < today]
	out = {
		"regime": regime,
		"regime_note": training.REGIME_NOTES.get(regime),
		"available": True,
		"company": company or None,
		"as_of": today,
		"count": len(capped),
		"truncated": len(found) > packets.ROW_CAP,
		"alerts": capped,
		"critical": critical,
		"overdue": overdue,
		"note": (
			f"Everything the compliance calendar currently has open for {regime}, as at {today}. "
			"Snoozed and dismissed items are excluded: a snooze is 'not this week' and a "
			"dismissal is somebody's recorded judgement that it did not need doing, and neither "
			"is a debt. This is a fact about TODAY rather than about the packet's period — an "
			"expired licence is expired now whatever the packet covers."
		),
	}
	if not capped:
		out["empty_note"] = (
			f"Nothing is open for {regime}. If the site has just upgraded, that may mean the "
			"sweep has not tagged its alerts yet rather than that there is nothing outstanding — "
			"refresh_compliance_alerts settles it in one call."
		)
	return out


def _period_of(filters: dict) -> tuple:
	"""(start, end, how we know) for a packet whose filters name a period differently."""
	start = str(filters.get("period_start") or "")
	end = str(filters.get("period_end") or "")
	if start or end:
		return start, end, "the packet's own period_start/period_end"

	fiscal_year = str(filters.get("fiscal_year") or "")
	if fiscal_year and compat.doctype_exists("Fiscal Year"):
		row = (
			frappe.db.get_value(
				"Fiscal Year", fiscal_year, ["year_start_date", "year_end_date"], as_dict=True
			)
			or {}
		)
		if row.get("year_start_date"):
			return (
				str(row["year_start_date"]),
				str(row.get("year_end_date") or ""),
				f"the dates on Fiscal Year {fiscal_year}",
			)
	return "", "", "ALL TIME — these filters name no period, so every record is included"


def packet_switch_names() -> tuple:
	"""Every `allow_<packet_type>` field the settings doctype must carry.

	Used by the settings invariant tests, which check that a packet type cannot
	ship without a way to turn it off.
	"""
	return tuple(f"allow_{name}" for name in packets.names())
