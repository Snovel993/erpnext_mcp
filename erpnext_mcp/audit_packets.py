# SPDX-License-Identifier: MIT
"""Assembling the evidence bundle an auditor is actually handed.

WHAT MAKES THIS DIFFERENT FROM A REPORT. A report answers a question. A packet is
the thing somebody carries into a room and defends, and the difference shows up in
three places:

  * **It pulls from the operational records, not from a copy.** The spray records
    in an FSMA packet are the Spray Logs. The worker facility records are the
    Housing Units. There is no shadow store this reads from, which is the whole
    Sprint 7 stance and the reason `compliance_fields.py` puts the applicator's
    name on the spray rather than in a compliance table beside it. A packet
    assembled from a shadow copy is a packet that can disagree with the records
    the auditor asks to see next.

  * **It refuses to be produced early.** See the kairotic gate below. This is the
    part that most distinguishes it from every "export to PDF" button ever
    written.

  * **It says what it could not find.** A section with no records says so, in the
    packet, with the reason. An FSMA packet whose traceability section is silently
    absent reads as an operation with nothing to declare rather than one whose
    BucketLog bridge is not installed, and an auditor will find the difference
    faster than the operator will.

THE KAIROTIC GATE, AND WHY IT IS A REFUSAL RATHER THAN A WARNING.

    A packet is not ready because the period ended. It is ready when the period
    is genuinely CLOSED — which means every corrective action raised inside it
    has been closed, and the period itself is actually over.

An FSMA packet produced while a corrective action from June is still open does not
contain a hole; it contains a claim. It says "here is the evidence of a compliant
operation for this period", hands it over, and is contradicted by the auditor's
first question — which will be about the open item, because open items are what
auditors look for. Producing it with a warning at the top does not help: warnings
are the first thing removed when a document is printed, and the auditor is reading
the evidence, not the covering note. So it refuses, and names every open action.

Overriding it is possible and is deliberately awkward: `allow_open_actions=true`
produces the packet with the open items listed in a section of their own at the
FRONT, because an operation that genuinely needs to hand something over
mid-remediation is better served by disclosing the remediation than by hiding it.

ADDING AN AUDIT TYPE is one `AuditPacketType` in the table below. Each names the
sections it pulls and the filters that scope them; the section builders are shared,
so a new type is a declaration rather than code.
"""

from __future__ import annotations

from dataclasses import dataclass

import frappe

from . import __version__, compat

#: Every section a packet can contain, and what each one reads. The order here is
#: the order they appear in the document, chosen so an auditor reads the claim
#: (policies, certificates) before the evidence (sprays, buckets, housing) and the
#: history (filings, audits) last.
SECTION_ORDER = (
	"open_actions",
	"policies",
	"certifications",
	"workforce",
	"spray_records",
	"water",
	"traceability",
	"housing",
	"filings",
	"audits",
)

#: Hard cap on any one section. A packet is meant to be read, and an appendix
#: with eleven thousand bucket log rows in it is not. A cap that bites is stated
#: in the packet, never silent — the same rule the compliance-packet framework
#: applies for the same reason.
SECTION_CAP = 750


@dataclass(frozen=True)
class AuditPacketType:
	"""One audit regime, and what it asks to see.

	`sections` names what goes in. `policy_categories`, `cert_types`,
	`audit_types` and `agencies` scope the evidence sections to what this regime
	is actually about — a DOL packet has no business containing a GlobalGAP
	certificate, and including it invites a question nobody wanted to answer.
	An empty tuple means "everything of that kind".
	"""

	key: str
	title: str
	regulator: str
	purpose: str
	sections: tuple
	policy_categories: tuple = ()
	cert_types: tuple = ()
	audit_types: tuple = ()
	agencies: tuple = ()

	def describe(self) -> dict:
		return {
			"audit_type": self.key,
			"title": self.title,
			"regulator": self.regulator,
			"purpose": self.purpose,
			"sections": [section for section in SECTION_ORDER if section in self.sections],
			"scoped_to": {
				"policy_categories": list(self.policy_categories) or ["(every category)"],
				"certificate_types": list(self.cert_types) or ["(every type)"],
				"audit_types": list(self.audit_types) or ["(every type)"],
				"agencies": list(self.agencies) or ["(every agency)"],
			},
			"switch": "allow_generate_audit_packet",
		}


TYPES: dict = {}


def register(spec: AuditPacketType) -> AuditPacketType:
	if spec.key in TYPES:
		raise RuntimeError(f"duplicate audit packet type {spec.key!r}")
	TYPES[spec.key] = spec
	return spec


def names() -> list:
	return sorted(TYPES)


def get(key: str):
	return TYPES.get(str(key or "").strip())


# ── the eight regimes ───────────────────────────────────────────────────────
register(
	AuditPacketType(
		key="FSMA",
		title="FSMA Produce Safety Rule",
		regulator="FDA (21 CFR 112), via a state-contracted inspector in Oregon",
		purpose=(
			"Worker health and hygiene, agricultural water, worker facilities, and "
			"traceability from the field to the shipment. The inspection an operation "
			"gets whether or not anybody buys on certification."
		),
		sections=(
			"open_actions",
			"policies",
			"certifications",
			"workforce",
			"water",
			"traceability",
			"housing",
			"spray_records",
			"audits",
		),
		policy_categories=(
			"Harvest Hygiene",
			"Water Testing",
			"Worker Training",
			"Equipment Sanitation",
			"Recall and Traceability",
			"Housing",
		),
		audit_types=("FSMA", "FDA", "ODA"),
	)
)

register(
	AuditPacketType(
		key="GAP",
		title="USDA Good Agricultural Practices",
		regulator="USDA AMS, or a licensed third-party auditor",
		purpose=(
			"The buyer-driven food safety audit. Broadly FSMA's ground with more weight "
			"on documented procedures and on the traceability exercise being timed."
		),
		sections=(
			"open_actions",
			"policies",
			"certifications",
			"workforce",
			"water",
			"traceability",
			"spray_records",
			"audits",
		),
		policy_categories=(
			"Harvest Hygiene",
			"Water Testing",
			"Worker Training",
			"Equipment Sanitation",
			"Recall and Traceability",
			"Food Defense",
		),
		cert_types=("GAP", "GlobalGAP", "PrimusGFS", "Food Safety Training", "Water Test Certification"),
		audit_types=("GAP", "GlobalGAP", "PrimusGFS", "Buyer Audit", "Internal Audit"),
	)
)

register(
	AuditPacketType(
		key="GlobalGAP",
		title="GLOBALG.A.P. Integrated Farm Assurance",
		regulator="A GLOBALG.A.P.-approved certification body",
		purpose=(
			"The export and retailer-facing scheme. Everything GAP asks plus worker "
			"welfare and a documented environmental position."
		),
		sections=(
			"open_actions",
			"policies",
			"certifications",
			"workforce",
			"water",
			"traceability",
			"housing",
			"spray_records",
			"audits",
		),
		cert_types=("GlobalGAP", "GAP", "PrimusGFS", "Organic", "Food Safety Training"),
		audit_types=("GlobalGAP", "GAP", "PrimusGFS", "Buyer Audit"),
	)
)

register(
	AuditPacketType(
		key="OSHA",
		title="Worker safety — OSHA and Oregon OSHA",
		regulator="Oregon OSHA (OAR 437), or federal OSHA",
		purpose=(
			"Heat illness prevention, field sanitation, agricultural labor housing, "
			"pesticide handler protection, and the 300A log. The inspection that "
			"follows a complaint or an injury."
		),
		sections=("open_actions", "policies", "certifications", "workforce", "housing", "spray_records", "filings", "audits"),
		policy_categories=("Worker Safety", "Worker Training", "Housing"),
		cert_types=("First Aid / CPR", "Applicator License", "Food Safety Training"),
		audit_types=("OSHA", "ODA"),
		agencies=("OSHA", "DOL", "WA-L&I"),
	)
)

register(
	AuditPacketType(
		key="DOL",
		title="Labor — MSPA, wage and hour, farm labor contracting",
		regulator="US Department of Labor Wage and Hour Division; Oregon BOLI",
		purpose=(
			"Employment eligibility, wage-law jurisdiction, farm labor contractor "
			"licensing, and the housing an employer provided. What a wage claim or an "
			"MSPA investigation asks for."
		),
		sections=("open_actions", "policies", "certifications", "workforce", "housing", "traceability", "filings", "audits"),
		policy_categories=("Worker Training", "Worker Safety", "Housing"),
		cert_types=("Farm Labor Contractor License", "Commercial Driver License"),
		audit_types=("DOL", "OSHA"),
		agencies=("DOL", "OR-BOLI", "IRS", "OR-DOR", "WA-L&I"),
	)
)

register(
	AuditPacketType(
		key="EPA",
		title="Pesticides — FIFRA, the Worker Protection Standard, and ODA",
		regulator="EPA; Oregon Department of Agriculture (ORS 634)",
		purpose=(
			"Application records, applicator licensing, restricted-entry and "
			"pre-harvest intervals, and the drift conditions on the day. What a drift "
			"complaint or a residue detection is investigated from."
		),
		sections=("open_actions", "policies", "certifications", "spray_records", "water", "workforce", "filings", "audits"),
		policy_categories=("Spray SOP", "Worker Training", "Water Testing"),
		cert_types=("Applicator License",),
		audit_types=("EPA", "ODA"),
		agencies=("EPA", "ODA"),
	)
)

register(
	AuditPacketType(
		key="USDA_NIFA",
		title="USDA NIFA — grant and programme reporting",
		regulator="USDA National Institute of Food and Agriculture",
		purpose=(
			"The compliance posture a grant application asserts and a programme review "
			"verifies. Certifications held, procedures in force, filings made."
		),
		sections=("open_actions", "policies", "certifications", "filings", "audits", "workforce"),
		agencies=("USDA", "ODA", "IRS"),
	)
)

register(
	AuditPacketType(
		key="Other",
		title="Everything on file for the period",
		regulator="Whoever asked",
		purpose=(
			"An unscoped bundle, for a buyer's own audit or a question nobody has a "
			"template for. Wider than any of the named types and correspondingly "
			"harder to read — prefer a named type where one fits."
		),
		sections=SECTION_ORDER,
	)
)


# ── section builders ────────────────────────────────────────────────────────
def _rows(doctype: str, filters, fields, order_by: str = "", limit: int = SECTION_CAP + 1) -> list:
	if not compat.doctype_exists(doctype):
		return []
	return [
		dict(row)
		for row in frappe.db.get_all(
			doctype,
			filters=filters,
			fields=compat.existing_fields(doctype, fields),
			order_by=order_by or "modified desc",
			limit=limit,
		)
		or []
	]


def _section(title: str, purpose: str, rows: list, columns: tuple, absent: str = "") -> dict:
	"""One section, with its cap and its emptiness both stated rather than implied."""
	truncated = len(rows) > SECTION_CAP
	kept = rows[:SECTION_CAP]
	out = {
		"title": title,
		"purpose": purpose,
		"columns": list(columns),
		"row_count": len(kept),
		"rows": kept,
		"truncated": truncated,
	}
	if truncated:
		out["truncation_note"] = (
			f"More than {SECTION_CAP} records matched and this section holds the first "
			f"{SECTION_CAP}. THIS PACKET IS INCOMPLETE for this section — narrow the period "
			"before relying on it."
		)
	if not kept:
		out["empty_note"] = absent or (
			"No records matched for this period. That is a statement about the period, not "
			"about whether the records exist — check the dates before concluding anything."
		)
	return out


def _company_filter(company: str, fieldname: str = "company") -> dict:
	return {fieldname: company} if company else {}


def _policies(spec: AuditPacketType, company: str, start: str, end: str) -> dict:
	"""Every procedure that was IN FORCE during the period.

	In force during the period, not in force today. A packet covering last summer
	wants the procedure the crew was actually working to last summer, which may
	well have been superseded since — and presenting today's SOP as evidence about
	last July is the single easiest way to be caught rewriting history.
	"""
	filters = _company_filter(company)
	rows = []
	for row in _rows(
		"Compliance Policy",
		filters,
		("name", "policy_name", "category", "version", "status", "effective_date", "review_due_date", "policy_owner", "superseded_by", "attached_document"),
		order_by="category asc, policy_name asc",
	):
		if spec.policy_categories and str(row.get("category") or "") not in spec.policy_categories:
			continue
		if str(row.get("status") or "") == "Draft":
			# A draft was never adopted. Presenting one as a procedure in force
			# would be misleading in exactly the direction that matters.
			continue
		effective = str(row.get("effective_date") or "")
		if effective and effective > end:
			continue
		rows.append(
			{
				"policy": row["name"],
				"category": row.get("category"),
				"version": row.get("version"),
				"status": row.get("status"),
				"effective_date": effective or None,
				"in_force_at_period_end": str(row.get("status") or "") == "Active",
				"superseded_by": row.get("superseded_by") or None,
				"document_attached": bool(row.get("attached_document")),
			}
		)
	return _section(
		"Written procedures in force",
		(
			"The procedures the operation was working to during this period — including any "
			"since superseded, because the crew was working to those at the time and today's "
			"version is not evidence about last July. Drafts are excluded: one was never adopted."
		),
		rows,
		("policy", "category", "version", "status", "effective_date", "document_attached"),
		absent="No compliance policies are recorded for this company and audit type at all.",
	)


def _certifications(spec: AuditPacketType, company: str, start: str, end: str) -> dict:
	"""Certificates that were VALID at some point during the period."""
	rows = []
	for row in _rows(
		"Certification",
		_company_filter(company),
		("name", "cert_name", "cert_type", "status", "holder", "issuing_body", "issued_date", "expiration_date", "certificate_number", "attached_certificate"),
		order_by="expiration_date desc",
	):
		if spec.cert_types and str(row.get("cert_type") or "") not in spec.cert_types:
			continue
		issued = str(row.get("issued_date") or "")
		expires = str(row.get("expiration_date") or "")
		if expires and expires < start:
			# Expired before the period began. Not evidence about the period.
			continue
		if issued and issued > end:
			continue
		covered = not expires or expires >= end
		rows.append(
			{
				"certificate": row["name"],
				"type": row.get("cert_type"),
				"holder": row.get("holder"),
				"issuing_body": row.get("issuing_body"),
				"number": row.get("certificate_number"),
				"issued": issued or None,
				"expires": expires or None,
				"covered_whole_period": covered,
				"document_attached": bool(row.get("attached_certificate")),
			}
		)
	gaps = [row["certificate"] for row in rows if not row["covered_whole_period"]]
	section = _section(
		"Certificates and licences",
		(
			"Every certificate that was valid at some point in the period. "
			"`covered_whole_period` is false where one expired part way through, which is a "
			"gap in coverage rather than an absence of evidence — it is shown rather than "
			"filtered out, because an auditor who finds it themselves asks a harder question."
		),
		rows,
		("certificate", "type", "holder", "issued", "expires", "covered_whole_period"),
		absent="No certificate of the types this audit is scoped to was valid during the period.",
	)
	if gaps:
		section["coverage_gaps"] = gaps
	return section


def _workforce(spec: AuditPacketType, company: str, start: str, end: str) -> dict:
	"""Employment eligibility and wage-law jurisdiction, per person.

	Reads the compliance fields `install_compliance_fields` puts ON the Employee
	record — not a parallel HR compliance table, because there is not one. If the
	fields are not installed the section says so by name, which is a far more
	useful answer than an empty table.
	"""
	if not compat.doctype_exists("Employee"):
		return _section(
			"Workforce eligibility",
			"Employment eligibility, withholding and the wage law governing each person.",
			[],
			("employee", "i9_status", "w4_status", "jurisdiction"),
			absent=(
				"This site has no Employee register — no HR app is installed — so there is "
				"nothing to report. This is not evidence of a compliant workforce."
			),
		)
	if not compat.has_field("Employee", "i9_status"):
		return _section(
			"Workforce eligibility",
			"Employment eligibility, withholding and the wage law governing each person.",
			[],
			("employee", "i9_status", "w4_status", "jurisdiction"),
			absent=(
				"The compliance fields are not installed on this site's Employee doctype, so "
				"I-9 and W-4 status cannot be reported. Run install_compliance_fields. Until "
				"then this section is ABSENT rather than empty, and an auditor should be told so."
			),
		)

	filters = _company_filter(company)
	if compat.has_field("Employee", "status"):
		filters["status"] = "Active"
	rows = []
	for row in _rows(
		"Employee",
		filters,
		("name", "employee_name", "company", "status", "date_of_joining", "i9_status", "w4_status", "jurisdiction", "flc_license_status", "flc_license_expiration"),
		order_by="employee_name asc",
	):
		rows.append(
			{
				"employee": row["name"],
				"name": row.get("employee_name"),
				"joined": str(row.get("date_of_joining") or "") or None,
				"i9_status": row.get("i9_status") or "(unrecorded)",
				"w4_status": row.get("w4_status") or "(unrecorded)",
				"jurisdiction": row.get("jurisdiction") or "(unrecorded)",
				"flc_license_status": row.get("flc_license_status") or None,
				"flc_license_expiration": str(row.get("flc_license_expiration") or "") or None,
			}
		)
	section = _section(
		"Workforce eligibility",
		(
			"I-9 and W-4 status and the wage law governing each active employee, read from the "
			"employee record itself. There is no separate HR compliance table — the columns are "
			"on the register payroll runs off, which is what makes them true."
		),
		rows,
		("employee", "name", "i9_status", "w4_status", "jurisdiction"),
	)
	problems = [row["employee"] for row in rows if row["i9_status"] in ("Expired", "(unrecorded)")]
	if problems:
		section["employees_without_a_verified_i9"] = problems
		section["problem_note"] = (
			f"{len(problems)} active employee(s) have an expired or unrecorded I-9. This is "
			"disclosed in the packet rather than filtered out of it: an auditor who finds it "
			"themselves asks a much harder question than one who was shown it."
		)
	return section


def _spray_records(spec: AuditPacketType, company: str, start: str, end: str) -> dict:
	"""Pesticide applications in the period, from the Spray Log itself."""
	if not compat.doctype_exists("Spray Log"):
		return _section(
			"Pesticide applications",
			"Every application made in the period, with the applicator, the product and the intervals.",
			[],
			("spray", "date", "applicator_name", "epa_reg_number", "rei_hours", "phi_hours"),
			absent=(
				"farm_precision_ag is not installed on this site, so there is no Spray Log to "
				"read. If applications were made in this period they are recorded somewhere "
				"else, and this packet does not contain them."
			),
		)
	date_field = compat.first_field("Spray Log", "application_date", "spray_date", "date", "posting_date")
	filters = _company_filter(company)
	if date_field:
		filters[date_field] = ("between", (start, end))
	rows = []
	for row in _rows(
		"Spray Log",
		filters,
		(
			"name",
			date_field or "creation",
			"field",
			"block",
			"product",
			"applicator_name",
			"epa_reg_number",
			"rei_hours",
			"phi_hours",
			"weather_temp_f",
			"weather_wind_mph",
			"wind_direction",
			"target_pest",
		),
		order_by=f"{date_field} asc" if date_field else "creation asc",
	):
		rows.append(
			{
				"spray": row["name"],
				"date": str(row.get(date_field) or row.get("creation") or "")[:10] or None,
				"block": row.get("field") or row.get("block"),
				"product": row.get("product"),
				"applicator_name": row.get("applicator_name") or "(unrecorded)",
				"epa_reg_number": row.get("epa_reg_number") or "(unrecorded)",
				"rei_hours": row.get("rei_hours"),
				"phi_hours": row.get("phi_hours"),
				"target_pest": row.get("target_pest"),
				"wind_mph": row.get("weather_wind_mph"),
				"wind_direction": row.get("wind_direction"),
				"temp_f": row.get("weather_temp_f"),
			}
		)
	section = _section(
		"Pesticide applications",
		(
			"Every application in the period as the Spray Log records it. The applicator, the "
			"registration number and the intervals are columns ON that record rather than a "
			"compliance copy of it — which is why they cannot have drifted from what was "
			"actually done."
		),
		rows,
		("spray", "date", "block", "product", "applicator_name", "epa_reg_number", "rei_hours", "phi_hours"),
	)
	incomplete = [row["spray"] for row in rows if row["applicator_name"] == "(unrecorded)" or row["epa_reg_number"] == "(unrecorded)"]
	if incomplete:
		section["incomplete_records"] = incomplete
		section["problem_note"] = (
			f"{len(incomplete)} application(s) predate the compliance fields and have no "
			"applicator or registration number. They cannot be completed retroactively without "
			"inventing facts, and they are shown as they are."
		)
	return section


def _water(spec: AuditPacketType, company: str, start: str, end: str) -> dict:
	"""Agricultural water testing, per block and per zone."""
	rows = []
	for row in _rows(
		"Field",
		_company_filter(company, "owning_entity"),
		("name", "field_name", "owning_entity", "crop", "condition", "water_test_last_date", "last_spray_date", "food_safety_zone", "worker_hygiene_station_present"),
		order_by="field_name asc",
	):
		tested = str(row.get("water_test_last_date") or "")
		rows.append(
			{
				"block": row["name"],
				"crop": row.get("crop"),
				"condition": row.get("condition"),
				"water_test_last_date": tested or None,
				"tested_within_period": bool(tested and start <= tested <= end),
				"current_at_period_end": bool(tested and tested >= str(frappe.utils.add_days(frappe.utils.getdate(end), -90))),
				"last_spray_date": str(row.get("last_spray_date") or "") or None,
				"worker_hygiene_station": compat.checked(row.get("worker_hygiene_station_present")),
			}
		)
	for row in _rows(
		"Irrigation Zone",
		_company_filter(company, "owning_entity"),
		("name", "zone_name", "field", "water_source", "water_right_id", "water_test_last_date", "water_source_class", "chlorination_active"),
		order_by="zone_name asc",
	):
		tested = str(row.get("water_test_last_date") or "")
		rows.append(
			{
				"block": f"{row['name']} (zone)",
				"crop": row.get("water_source"),
				"condition": row.get("water_source_class"),
				"water_test_last_date": tested or None,
				"tested_within_period": bool(tested and start <= tested <= end),
				"current_at_period_end": bool(tested and tested >= str(frappe.utils.add_days(frappe.utils.getdate(end), -90))),
				"last_spray_date": None,
				"worker_hygiene_station": compat.checked(row.get("chlorination_active")),
			}
		)
	section = _section(
		"Agricultural water",
		(
			"Water test dates per block and per irrigation zone. `current_at_period_end` applies "
			"the Produce Safety Rule Subpart E ninety-day window to the END of the period, "
			"which is the question an inspector asks — not to today, which would flatter a "
			"packet about last season."
		),
		rows,
		("block", "water_test_last_date", "tested_within_period", "current_at_period_end", "worker_hygiene_station"),
		absent="No Field or Irrigation Zone records exist for this company.",
	)
	stale = [row["block"] for row in rows if not row["current_at_period_end"]]
	if stale:
		section["without_a_current_test_at_period_end"] = stale
	return section


def _traceability(spec: AuditPacketType, company: str, start: str, end: str) -> dict:
	"""The bucket-to-shipment chain, or a plain statement that it is not here."""
	doctype = "Bucket Log Entry"
	if not compat.doctype_exists(doctype):
		return _section(
			"Harvest traceability",
			"Bucket → picker → crew → block → bin → shipment, the FSMA critical tracking events.",
			[],
			("entry", "date", "picker_id", "crew_id", "block_id", "bin_id", "shipment_id"),
			absent=(
				"The BucketLog bridge is not installed on this site, so the bucket-level chain "
				"of custody is not in this packet. Traceability for this period exists in the "
				"BucketLog app's own export and has to be supplied separately. Saying so is "
				"more use than an empty table, which reads as an operation with nothing to "
				"declare."
			),
		)
	date_field = compat.first_field(doctype, "logged_at", "log_date", "date", "posting_date", "creation")
	filters = _company_filter(company)
	if date_field and date_field != "creation":
		filters[date_field] = ("between", (start, end))
	rows = []
	for row in _rows(
		doctype,
		filters,
		("name", date_field or "creation", "picker_id", "crew_id", "block_id", "bin_id", "shipment_id", "disposition"),
		order_by=f"{date_field} asc" if date_field else "creation asc",
	):
		rows.append(
			{
				"entry": row["name"],
				"date": str(row.get(date_field) or row.get("creation") or "")[:10] or None,
				"picker_id": row.get("picker_id") or "(unlinked)",
				"crew_id": row.get("crew_id") or "(unlinked)",
				"block_id": row.get("block_id") or "(unlinked)",
				"bin_id": row.get("bin_id") or "(unlinked)",
				"shipment_id": row.get("shipment_id") or "(unlinked)",
				"disposition": row.get("disposition"),
			}
		)
	section = _section(
		"Harvest traceability",
		(
			"The critical tracking events the FSMA Food Traceability Rule asks for, one row per "
			"bucket. A buyer's mock recall is timed, and the chain is only as good as its "
			"weakest link — unlinked columns are shown as such."
		),
		rows,
		("entry", "date", "picker_id", "crew_id", "block_id", "bin_id", "shipment_id"),
	)
	broken = [
		row["entry"]
		for row in rows
		if "(unlinked)" in (row["block_id"], row["bin_id"], row["shipment_id"])
	]
	if broken:
		section["chain_breaks"] = broken[:100]
		section["problem_note"] = (
			f"{len(broken)} entry/entries are missing a block, bin or shipment link. Each one is "
			"a bucket that cannot be traced end to end, which is the exercise the auditor times."
		)
	return section


def _housing(spec: AuditPacketType, company: str, start: str, end: str) -> dict:
	"""Worker facilities: what there is, its condition, and who slept in it."""
	units = []
	for row in _rows(
		"Housing Unit",
		_company_filter(company, "owning_entity"),
		("name", "unit_name", "unit_type", "parcel", "capacity", "condition", "fsma_worker_facility", "or_housing_law_compliant", "max_occupants_per_or_law", "last_habitability_inspection", "smoke_detector_last_test", "co_detector_last_test"),
		order_by="parcel asc, unit_name asc",
	):
		units.append(
			{
				"unit": row["name"],
				"type": row.get("unit_type"),
				"parcel": row.get("parcel"),
				"capacity": row.get("capacity"),
				"lawful_occupancy": row.get("max_occupants_per_or_law"),
				"condition": row.get("condition"),
				"fsma_worker_facility": compat.checked(row.get("fsma_worker_facility")),
				"last_habitability_inspection": str(row.get("last_habitability_inspection") or "") or None,
				"smoke_detector_last_test": str(row.get("smoke_detector_last_test") or "") or None,
				"co_detector_last_test": str(row.get("co_detector_last_test") or "") or None,
			}
		)
	assignments = _rows(
		"Housing Assignment",
		{"assigned_date": ("<=", end)},
		("name", "unit", "employee", "employee_name", "assigned_date", "end_date", "housing_deduction_from_wages", "deposit_paid", "deposit_returned"),
		order_by="assigned_date asc",
	)
	occupancy = []
	for row in assignments:
		ended = str(row.get("end_date") or "")
		if ended and ended < start:
			continue
		occupancy.append(
			{
				"assignment": row["name"],
				"unit": row.get("unit"),
				"person": row.get("employee_name") or row.get("employee"),
				"from": str(row.get("assigned_date") or "") or None,
				"to": ended or "(still current)",
				"wage_deduction": row.get("housing_deduction_from_wages") or "Unknown",
			}
		)

	section = _section(
		"Worker facilities and occupancy",
		(
			"Every building, its condition and detector tests, and who slept in it during the "
			"period. The occupancy roster is the audit trail behind an IRS Section 119 exclusion "
			"and the answer to an ORS 653 wage-deduction claim, and it exists nowhere else."
		),
		units,
		("unit", "type", "parcel", "capacity", "condition", "last_habitability_inspection"),
		absent="No housing units are registered for this company.",
	)
	section["occupancy"] = occupancy[:SECTION_CAP]
	section["occupancy_count"] = len(occupancy[:SECTION_CAP])
	uninspected = [unit["unit"] for unit in units if not unit["last_habitability_inspection"]]
	if uninspected:
		section["never_inspected"] = uninspected
	undeclared = [row["assignment"] for row in occupancy if row["wage_deduction"] == "Unknown"]
	if undeclared:
		section["wage_deduction_unrecorded"] = undeclared[:100]
	return section


def _filings(spec: AuditPacketType, company: str, start: str, end: str) -> dict:
	"""Filings submitted during the period."""
	rows = []
	for row in _rows(
		"Regulatory Filing",
		_company_filter(company),
		("name", "filing_name", "agency", "filing_type", "status", "period_covered", "submission_date", "docket_number", "response_received_date", "attached_filing"),
		order_by="submission_date asc",
	):
		if spec.agencies and str(row.get("agency") or "") not in spec.agencies:
			continue
		submitted = str(row.get("submission_date") or "")
		if not submitted or submitted < start or submitted > end:
			continue
		if str(row.get("status") or "") == "Draft":
			continue
		rows.append(
			{
				"filing": row["name"],
				"agency": row.get("agency"),
				"type": row.get("filing_type"),
				"period_covered": row.get("period_covered"),
				"submitted": submitted,
				"docket_number": row.get("docket_number"),
				"response_received": str(row.get("response_received_date") or "") or None,
				"status": row.get("status"),
				"document_attached": bool(row.get("attached_filing")),
			}
		)
	return _section(
		"Regulatory filings submitted",
		(
			"What went to an agency during this period, with the confirmation number that "
			"proves it. A filing nobody can prove was made is a filing that was not made, and "
			"the agency's position in a dispute is that they have no record. Drafts are "
			"excluded: nothing was sent."
		),
		rows,
		("filing", "agency", "type", "submitted", "docket_number", "status"),
		absent="No filing was submitted to a relevant agency during this period.",
	)


def _audits(spec: AuditPacketType, company: str, start: str, end: str) -> dict:
	"""Audits during the period, and every corrective action with its closure."""
	rows = []
	actions = []
	for row in _rows(
		"Audit Event",
		_company_filter(company),
		("name", "audit_name", "audit_type", "auditor", "audit_date", "result", "corrective_actions_closed", "attached_report"),
		order_by="audit_date asc",
	):
		if spec.audit_types and str(row.get("audit_type") or "") not in spec.audit_types:
			continue
		audit_date = str(row.get("audit_date") or "")
		if not audit_date or audit_date < start or audit_date > end:
			continue
		rows.append(
			{
				"audit": row["name"],
				"type": row.get("audit_type"),
				"auditor": row.get("auditor"),
				"date": audit_date,
				"result": row.get("result"),
				"closed": str(row.get("corrective_actions_closed") or "") or None,
				"report_attached": bool(row.get("attached_report")),
			}
		)
		for index, action in enumerate(_actions_of(row["name"]), start=1):
			actions.append({"audit": row["name"], "index": index, **action})

	section = _section(
		"Audits and inspections",
		(
			"Every audit and inspection in the period, with each corrective action and its "
			"closure. An operation is not judged on having no findings — every audit produces "
			"some — it is judged on closing them, which is what the second table shows."
		),
		rows,
		("audit", "type", "auditor", "date", "result", "closed"),
		absent="No audit or inspection of a relevant type took place during this period.",
	)
	section["corrective_actions"] = actions[:SECTION_CAP]
	section["corrective_action_count"] = len(actions[:SECTION_CAP])
	section["all_closed"] = not any(action["status"] in ("Open", "In Progress") for action in actions)
	return section


def _actions_of(audit: str) -> list:
	try:
		doc = frappe.get_doc("Audit Event", audit)
	except Exception:  # pragma: no cover
		return []
	return [
		{
			"finding": row.get("finding"),
			"severity": row.get("severity") or "Minor",
			"status": row.get("status") or "Open",
			"due_date": str(row.get("due_date") or "") or None,
			"closed_date": str(row.get("closed_date") or "") or None,
			"corrective_action": row.get("corrective_action"),
			"evidence": row.get("evidence"),
		}
		for row in doc.get("corrective_actions_required") or []
	]


def _open_actions_section(spec: AuditPacketType, company: str, start: str, end: str) -> dict:
	"""Only ever present when the kairotic gate was overridden. See `open_actions`."""
	rows = [
		{
			"audit": entry["audit"],
			"finding": entry["finding"],
			"severity": entry["severity"],
			"due_date": entry["due_date"],
			"days_overdue": entry["days_overdue"],
			"assigned_to": entry.get("assigned_to"),
		}
		for entry in open_actions(company, start, end)
	]
	return _section(
		"OPEN CORRECTIVE ACTIONS — remediation in progress",
		(
			"This packet was produced while these findings were still open, because somebody "
			"passed allow_open_actions=true. They are at the FRONT rather than buried: an "
			"operation that has to hand something over mid-remediation is far better served by "
			"disclosing the remediation than by having the auditor find it."
		),
		rows,
		("audit", "finding", "severity", "due_date", "days_overdue"),
		absent="No corrective action was open. This section should not have been produced.",
	)


_BUILDERS = {
	"open_actions": _open_actions_section,
	"policies": _policies,
	"certifications": _certifications,
	"workforce": _workforce,
	"spray_records": _spray_records,
	"water": _water,
	"traceability": _traceability,
	"housing": _housing,
	"filings": _filings,
	"audits": _audits,
}


# ── the kairotic gate ───────────────────────────────────────────────────────
def open_actions(company: str, start: str, end: str) -> list:
	"""Corrective actions raised in the period that are still open.

	Scoped to the period on purpose. An action from an audit two years after this
	packet's period has nothing to do with whether this period is closed, and
	blocking on it would make a packet about 2024 impossible to produce forever.
	"""
	if not compat.doctype_exists("Audit Event"):
		return []
	today = frappe.utils.today()
	out = []
	for row in _rows(
		"Audit Event",
		{**_company_filter(company), "corrective_actions_closed": ("is", "not set")},
		("name", "audit_name", "audit_type", "audit_date", "company"),
		order_by="audit_date asc",
		limit=SECTION_CAP,
	):
		audit_date = str(row.get("audit_date") or "")
		if not audit_date or audit_date < start or audit_date > end:
			continue
		try:
			doc = frappe.get_doc("Audit Event", row["name"])
		except Exception:  # pragma: no cover
			continue
		for index, action in doc.open_actions():
			due = str(action.get("due_date") or "")
			overdue = None
			if due:
				try:
					overdue = max(0, int(frappe.utils.date_diff(today, due)))
				except Exception:  # pragma: no cover
					overdue = None
			out.append(
				{
					"audit": row["name"],
					"audit_type": row.get("audit_type"),
					"index": index,
					"finding": action.get("finding"),
					"severity": action.get("severity") or "Minor",
					"status": action.get("status") or "Open",
					"due_date": due or None,
					"days_overdue": overdue,
					"assigned_to": action.get("assigned_to"),
				}
			)
	return out


def readiness(spec: AuditPacketType, company: str, start: str, end: str, today: str = "") -> dict:
	"""Whether the period is genuinely CLOSED, and what is stopping it if not.

	Two conditions, and neither is "the end date has passed on a calendar":

	  1. The period is actually over. A packet covering a period that has not
	     finished is a packet about records that do not exist yet.
	  2. Every corrective action raised inside it has been closed. This is the
	     kairotic half — the packet asserts a compliant period, and an open finding
	     inside that period contradicts the assertion.
	"""
	today = today or frappe.utils.today()
	blockers = []
	if end > today:
		blockers.append(
			{
				"blocker": "period_not_over",
				"detail": (
					f"This packet covers up to {end}, which is in the future — today is {today}. "
					"The records for the rest of it do not exist yet, so a packet produced now "
					"would assert evidence of a period that has not happened."
				),
			}
		)
	pending = open_actions(company, start, end)
	if pending:
		blockers.append(
			{
				"blocker": "open_corrective_actions",
				"detail": (
					f"{len(pending)} corrective action(s) raised by audits INSIDE this period are "
					"still open. This packet would assert a compliant period and be contradicted "
					"by the auditor's first question, which will be about the open item — open "
					"items are what auditors look for."
				),
				"actions": pending,
			}
		)
	return {
		"ready": not blockers,
		"today": today,
		"blockers": blockers,
		"open_action_count": len(pending),
	}


# ── assembly ────────────────────────────────────────────────────────────────
def build(spec: AuditPacketType, company: str, start: str, end: str, allow_open_actions: bool = False) -> dict:
	"""Assemble one packet. Reads only; writes nothing."""
	sections = []
	for key in SECTION_ORDER:
		if key not in spec.sections:
			continue
		if key == "open_actions" and not allow_open_actions:
			# Only ever present on an overridden gate. On a clean packet there is
			# nothing to disclose and a section saying "none" would be noise.
			continue
		sections.append({"key": key, **_BUILDERS[key](spec, company, start, end)})

	counts = {section["key"]: section["row_count"] for section in sections}
	problems = []
	for section in sections:
		if section.get("problem_note"):
			problems.append({"section": section["key"], "detail": section["problem_note"]})
		if section.get("truncated"):
			problems.append({"section": section["key"], "detail": section["truncation_note"]})
		if section.get("empty_note") and not section["row_count"]:
			problems.append({"section": section["key"], "detail": section["empty_note"]})

	return {
		"audit_type": spec.key,
		"title": spec.title,
		"regulator": spec.regulator,
		"purpose": spec.purpose,
		"company": company or None,
		"period_start": start,
		"period_end": end,
		"sections": sections,
		"section_counts": counts,
		"total_records": sum(counts.values()),
		"disclosures": problems,
		"generated_at": frappe.utils.now(),
		"generated_by": str(frappe.session.user),
		"site": str(frappe.local.site),
		"generator": "erpnext_mcp",
		"generator_version": __version__,
		"produced_over_open_actions": bool(allow_open_actions),
	}


def document_sections(packet: dict):
	"""The packet as a (kind, payload) stream both renderers consume.

	One description, two output formats. The same shape `investment_report` uses,
	and for the same reason: a PDF and a DOCX that were built by two separate
	walks of the same data will differ within one release, and the difference will
	be discovered by whoever printed the wrong one.
	"""
	yield (
		"title",
		(
			f"{packet['title']} — Audit Packet",
			(
				packet["company"] or "all companies on this site",
				f"{packet['period_start']} to {packet['period_end']}",
				f"Prepared for: {packet['regulator']}",
			),
		),
	)
	yield ("paragraph", packet["purpose"])

	yield ("heading", "What is in this packet")
	yield (
		"table",
		(
			["Section", "Records"],
			[[section["title"], str(section["row_count"])] for section in packet["sections"]],
			("l", "r"),
		),
	)
	if packet["produced_over_open_actions"]:
		yield (
			"paragraph",
			"THIS PACKET WAS PRODUCED WHILE CORRECTIVE ACTIONS WERE STILL OPEN. They are listed "
			"in the first section. It is disclosed here rather than left to be discovered.",
		)
	if packet["disclosures"]:
		yield ("subheading", "Disclosures")
		yield ("bullets", [f"{entry['section']}: {entry['detail']}" for entry in packet["disclosures"]])

	for section in packet["sections"]:
		yield ("page_break", None)
		yield ("heading", section["title"])
		yield ("paragraph", section["purpose"])
		if not section["row_count"]:
			yield ("paragraph", section.get("empty_note") or "No records.")
			continue
		columns = section["columns"]
		yield (
			"table",
			(
				[column.replace("_", " ").title() for column in columns],
				[
					[_cell(row.get(column)) for column in columns]
					for row in section["rows"]
				],
				tuple("l" for _ in columns),
			),
		)
		if section.get("truncation_note"):
			yield ("paragraph", section["truncation_note"])
		if section.get("problem_note"):
			yield ("paragraph", section["problem_note"])
		if section.get("occupancy"):
			yield ("subheading", "Occupancy during the period")
			yield (
				"table",
				(
					["Assignment", "Unit", "Person", "From", "To", "Wage deduction"],
					[
						[
							row["assignment"],
							row["unit"],
							_cell(row["person"]),
							_cell(row["from"]),
							_cell(row["to"]),
							row["wage_deduction"],
						]
						for row in section["occupancy"]
					],
					("l", "l", "l", "l", "l", "l"),
				),
			)
		if section.get("corrective_actions"):
			yield ("subheading", "Corrective actions")
			yield (
				"table",
				(
					["Audit", "Finding", "Severity", "Status", "Due", "Closed"],
					[
						[
							row["audit"],
							_cell(row["finding"])[:120],
							row["severity"],
							row["status"],
							_cell(row["due_date"]),
							_cell(row["closed_date"]),
						]
						for row in section["corrective_actions"]
					],
					("l", "l", "l", "l", "l", "l"),
				),
			)

	yield ("page_break", None)
	yield ("heading", "Provenance")
	yield (
		"key_values",
		[
			("Audit type", packet["audit_type"]),
			("Company", packet["company"] or "all companies"),
			("Period", f"{packet['period_start']} to {packet['period_end']}"),
			("Records in this packet", str(packet["total_records"])),
			("Generated at", packet["generated_at"]),
			("Generated by", packet["generated_by"]),
			("Site", packet["site"]),
			("Generator", f"erpnext_mcp {packet['generator_version']}"),
		],
	)
	yield (
		"paragraph",
		"Every record in this packet was read from the operational document that produced it — "
		"the spray records ARE the spray logs, the worker facility records ARE the housing "
		"register, the traceability rows ARE the bucket log. Nothing here is a copy kept for "
		"compliance purposes, which is why nothing here can have drifted from what was actually "
		"done.",
	)
	yield (
		"paragraph",
		"A packet is refused on a period whose corrective actions are still open, and on a "
		"period that has not finished. Neither is a warning at the top of a document nobody "
		"reads; both are refusals.",
	)


def _cell(value) -> str:
	if value is None:
		return ""
	if isinstance(value, bool):
		return "yes" if value else "no"
	return str(value)
