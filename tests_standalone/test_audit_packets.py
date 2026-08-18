# SPDX-License-Identifier: MIT
"""The Audit Packet Generator and the Command Center — Sprint 7 Wave 4.

`EveryAuditType` IS THE HEART OF THIS FILE. Each of the eight regimes gets the
same round trip: assemble it, assert what came out, and — the part that catches
the failures nobody predicts — CHECK THAT THE PDF ACTUALLY RENDERS. A packet
that assembles into a dict and then dies in the renderer is a packet somebody
discovers on the morning of the audit, and a section builder that returns a shape
the table emitter cannot draw is the easiest way to produce one.

THE KAIROTIC GATE IS THE OTHER HALF. A packet asserts a compliant period. It is
refused on a period that has not finished, and on one whose corrective actions
are still open — and refused rather than warned, because a warning at the top of
a printed document is not read by the person the document is handed to. The
override exists, it is awkward, and what it produces is tested too: the open items
end up in a section at the FRONT, which is the honest way to hand over an
unfinished period.

SECTIONS SAY WHY THEY ARE EMPTY. An EPA packet on a site with no
farm_precision_ag has to say the app is not installed, not silently omit spray
records — because an absent section reads as an operation with nothing to
declare, and an auditor will find the difference faster than the operator
will. Bucket Log Entry is erpnext_mcp's own doctype since v0.44.0 (it is no
longer possible for it to be "not installed" on a site running this app), so
its traceability section is empty-for-the-period rather than absent-by-app —
see `TraceabilityIsErpnextMcpsOwnDoctype`.

THE COMMAND CENTER IS AN INSTALLER, NOT A FIXTURE, and `TheCommandCenter` runs
it three times. A fixture cannot look at what is already there, so an operator who
reordered their cards would get it silently put back on every migrate — which is
exactly why `test_hooks.py` forbids the `fixtures` hook by name.
"""

import json

from erpnext_mcp import audit_packets, compliance_fields, dashboard, install

from .fixtures import MAIN, OTHER, V12TestCase, install_hrms
from .harness import INSTALLED_DOCTYPES, STORE, add_field

ALL_ON = {
	f"allow_{name}": 1
	for name in (
		"generate_audit_packet",
		"list_audit_packet_types",
		"create_compliance_policy",
		"create_certification",
		"create_regulatory_filing",
		"create_audit_event",
		"update_audit_event",
		"close_audit_event",
		"create_parcel",
		"create_field",
		"create_housing_unit",
		"create_housing_assignment",
		"create_spray_application",
		"list_governance_documents",
		"get_governance_document_content",
	)
}

PERIOD = {"period_start": "2026-01-01", "period_end": "2026-06-30"}


class PacketTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def a_full_operation(self, close_the_audit=True):
		"""One of everything, so a packet has something in every section it asks for."""
		self.tool_data(
			"create_compliance_policy",
			{
				"policy_name": "Harvest Hygiene SOP",
				"category": "Harvest Hygiene",
				"company": MAIN,
				"version": "v3",
				"effective_date": "2025-06-01",
				"review_due_date": "2027-06-01",
			},
		)
		self.tool_data(
			"create_compliance_policy",
			{
				"policy_name": "Spray Drift Management SOP",
				"category": "Spray SOP",
				"company": MAIN,
				"version": "v2",
				"effective_date": "2025-06-01",
			},
		)
		self.tool_data(
			"create_compliance_policy",
			{
				"policy_name": "Worker Safety SOP",
				"category": "Worker Safety",
				"company": MAIN,
				"version": "v1",
				"effective_date": "2025-06-01",
			},
		)
		self.tool_data(
			"create_certification",
			{
				"cert_name": "GlobalGAP 2026",
				"cert_type": "GlobalGAP",
				"company": MAIN,
				"holder": MAIN,
				"issuing_body": "Primus Auditing Ops",
				"issued_date": "2025-09-01",
				"expiration_date": "2027-09-01",
			},
		)
		self.tool_data(
			"create_certification",
			{
				"cert_name": "Applicator — R. Mendez",
				"cert_type": "Applicator License",
				"company": MAIN,
				"holder": "R. Mendez",
				"issuing_body": "Oregon Department of Agriculture",
				"issued_date": "2025-01-01",
				"expiration_date": "2027-01-01",
			},
		)
		self.tool_data(
			"create_certification",
			{
				"cert_name": "FLC — R. Mendez",
				"cert_type": "Farm Labor Contractor License",
				"company": MAIN,
				"holder": "R. Mendez",
				"issued_date": "2025-01-01",
				"expiration_date": "2027-01-01",
			},
		)
		self.tool_data(
			"create_regulatory_filing",
			{
				"filing_name": "OSHA 300A 2025",
				"agency": "OSHA",
				"filing_type": "OSHA-300A",
				"company": MAIN,
				"submission_date": "2026-02-01",
				"docket_number": "OSHA-2026-1",
			},
		)
		self.tool_data(
			"create_regulatory_filing",
			{
				"filing_name": "Pesticide Report 2026-Q1",
				"agency": "ODA",
				"filing_type": "Pesticide-Application-Report",
				"company": MAIN,
				"submission_date": "2026-04-01",
				"docket_number": "ODA-2026-7",
			},
		)
		self.tool_data(
			"create_parcel",
			{
				"owning_entity": MAIN,
				"parcel_name": "Mill Creek",
				"acreage": 131.43,
				"county": "Wasco",
				"state": "OR",
				"use_type": "Orchard",
			},
		)
		self.tool_data(
			"create_field",
			{
				"parcel": "Mill Creek",
				"field_name": "Yellow Camp Block 3",
				"acreage": 12.5,
				"variety": "Bing",
				"condition": "Good",
				"water_test_last_date": "2026-06-01",
				"last_spray_date": "2026-06-15",
			},
		)
		self.tool_data(
			"create_housing_unit",
			{
				"parcel": "Mill Creek",
				"unit_name": "MC-Cabin-01",
				"unit_type": "Cabin",
				"square_footage": 384,
				"capacity": 4,
				"condition": "Good",
				"fsma_worker_facility": True,
				"last_habitability_inspection": "2026-04-01",
				"smoke_detector_last_test": "2026-04-01",
				"co_detector_last_test": "2026-04-01",
			},
		)
		self.tool_data(
			"create_housing_assignment",
			{
				"unit": "MC-Cabin-01",
				"employee_name": "R. Mendez",
				"assigned_date": "2026-05-01",
				"housing_deduction_from_wages": "No",
			},
		)
		for audit_type in ("PrimusGFS", "OSHA", "ODA", "DOL", "FSMA", "USDA", "EPA"):
			self.tool_data(
				"create_audit_event",
				{
					"audit_name": f"{audit_type} 2026",
					"audit_type": audit_type,
					"audit_date": "2026-03-15",
					"auditor": "J. Reyes",
					"company": MAIN,
					"result": "Passed With Conditions",
					"corrective_actions": [
						{
							"finding": "Hand wash station in Block 4 had no soap at 0800",
							"severity": "Major",
							"due_date": "2026-04-15",
						}
					],
				},
			)
			if close_the_audit:
				self.tool_data(
					"update_audit_event",
					{
						"audit": f"{audit_type} 2026",
						"close_corrective_action": 1,
						"corrective_action": "restocked soap and added a daily check to the pre-harvest walk",
						"closed_date": "2026-04-10",
					},
				)
				self.tool_data(
					"close_audit_event",
					{
						"audit": f"{audit_type} 2026",
						"closure_note": "auditor confirmed the closure by email on 2026-04-12",
					},
				)

	def generate(self, audit_type="GAP", **overrides):
		payload = {"audit_type": audit_type, "company": MAIN, **PERIOD}
		payload.update(overrides)
		return self.tool_data("generate_audit_packet", payload)


# ── every audit type, round trip ────────────────────────────────────────────
class EveryAuditType(PacketTestCase):
	"""Tim's requirement, per regime: generate, assert the bundle, render the PDF."""

	def setUp(self):
		super().setUp()
		self.a_full_operation()

	def test_every_registered_type_generates_and_files_a_document(self):
		for audit_type in audit_packets.names():
			with self.subTest(audit_type=audit_type):
				data = self.generate(audit_type)
				self.assertTrue(data["created"])
				self.assertTrue(data["governance_document"])
				self.assertGreater(data["attachment"]["file_size"], 1000)

	def test_every_type_produces_a_pdf_that_actually_renders(self):
		"""A packet that assembles into a dict and dies in the renderer is one
		somebody discovers on the morning of the audit."""
		for audit_type in audit_packets.names():
			with self.subTest(audit_type=audit_type):
				spec = audit_packets.TYPES[audit_type]
				packet = audit_packets.build(spec, MAIN, PERIOD["period_start"], PERIOD["period_end"])
				from erpnext_mcp.tools import auditpacket

				content = auditpacket._render_pdf(packet, list(audit_packets.document_sections(packet)))
				self.assertTrue(content.startswith(b"%PDF-"), f"{audit_type} did not produce a PDF")
				self.assertIn(b"%%EOF", content[-64:])

	def test_every_type_produces_a_docx_that_actually_renders(self):
		"""DOCX is the secondary format and gets the same treatment, because a
		format nobody exercises is a format that breaks quietly."""
		for audit_type in audit_packets.names():
			with self.subTest(audit_type=audit_type):
				data = self.generate(audit_type, output_format="docx")
				self.assertTrue(data["attachment"]["file_name"].endswith(".docx"))
				self.assertGreater(data["attachment"]["file_size"], 500)

	def test_each_type_is_scoped_to_the_evidence_its_regulator_asks_for(self):
		"""A DOL packet has no business containing a GlobalGAP certificate —
		including it invites a question nobody wanted to answer."""
		dol = self._sections(self.generate("DOL"))
		certs = [row["certificate"] for row in dol["certifications"]["rows"]]
		self.assertIn("FLC — R. Mendez", certs)
		self.assertNotIn("GlobalGAP 2026", certs)

		gap = self._sections(self.generate("GAP"))
		gap_certs = [row["certificate"] for row in gap["certifications"]["rows"]]
		self.assertIn("GlobalGAP 2026", gap_certs)
		self.assertNotIn("FLC — R. Mendez", gap_certs)

	def test_the_epa_packet_carries_the_spray_sop_and_the_applicator_licence(self):
		sections = self._sections(self.generate("EPA"))
		self.assertIn("Spray Drift Management SOP", [row["policy"] for row in sections["policies"]["rows"]])
		self.assertIn(
			"Applicator — R. Mendez", [row["certificate"] for row in sections["certifications"]["rows"]]
		)

	def test_the_osha_packet_carries_the_housing_and_the_worker_safety_sop(self):
		sections = self._sections(self.generate("OSHA"))
		self.assertIn("Worker Safety SOP", [row["policy"] for row in sections["policies"]["rows"]])
		self.assertTrue(sections["housing"]["rows"])
		self.assertTrue(sections["housing"]["occupancy"])

	def test_the_other_type_is_everything_and_says_it_is_harder_to_read(self):
		spec = audit_packets.get("Other")
		self.assertEqual(set(spec.sections), set(audit_packets.SECTION_ORDER))
		self.assertIn("harder to read", spec.purpose)

	def _sections(self, data: dict) -> dict:
		return {section["key"]: section for section in data["packet"]["sections"]}


# ── the kairotic gate ───────────────────────────────────────────────────────
class TheKairoticGate(PacketTestCase):
	def test_it_refuses_a_period_whose_corrective_actions_are_still_open(self):
		self.a_full_operation(close_the_audit=False)
		message = self.tool_error("generate_audit_packet", {"audit_type": "GAP", "company": MAIN, **PERIOD})
		self.assertIn("not closed", message)
		self.assertIn("Hand wash station", message)
		self.assertIn("Nothing was created", message)

	def test_it_refuses_a_period_that_has_not_finished(self):
		"""A packet about records that do not exist yet."""
		self.a_full_operation()
		message = self.tool_error(
			"generate_audit_packet",
			{"audit_type": "GAP", "company": MAIN, "period_start": "2026-01-01", "period_end": "2027-12-31"},
		)
		self.assertIn("in the future", message)

	def test_an_open_action_OUTSIDE_the_period_does_not_block_it(self):
		"""An action from an audit two years after this packet's period has
		nothing to do with whether this period is closed — blocking on it would
		make a packet about 2024 impossible to produce forever."""
		self.a_full_operation()
		self.tool_data(
			"create_audit_event",
			{
				"audit_name": "Later Audit",
				"audit_type": "GAP",
				"audit_date": "2026-07-01",
				"company": MAIN,
				"corrective_actions": [
					{"finding": "Something from July", "severity": "Major", "due_date": "2026-07-20"}
				],
			},
		)
		self.assertTrue(self.generate("GAP")["created"])

	def test_the_override_produces_the_packet_with_the_open_items_at_the_front(self):
		"""The honest way to hand over an unfinished period."""
		self.a_full_operation(close_the_audit=False)
		data = self.generate("GAP", allow_open_actions=True)
		self.assertTrue(data["packet"]["produced_over_open_actions"])
		self.assertEqual(data["packet"]["sections"][0]["key"], "open_actions")
		self.assertIn("OPEN CORRECTIVE ACTIONS", data["packet"]["sections"][0]["title"])
		self.assertIn("OVER OPEN CORRECTIVE ACTIONS", data["warning"])

	def test_a_clean_packet_has_no_open_actions_section_at_all(self):
		"""A section saying 'none' would be noise on the ninety-nine packets that
		are fine."""
		self.a_full_operation()
		keys = [section["key"] for section in self.generate("GAP")["packet"]["sections"]]
		self.assertNotIn("open_actions", keys)

	def test_the_dry_run_reports_the_blockers_without_writing(self):
		self.a_full_operation()
		before = len(STORE.rows("Governance Document"))
		data = self.generate("GAP", dry_run=True)
		self.assertTrue(data["dry_run"])
		self.assertFalse(data["created"])
		self.assertTrue(data["readiness"]["ready"])
		self.assertEqual(len(STORE.rows("Governance Document")), before)


# ── idempotence and filing ──────────────────────────────────────────────────
class FilingThePacket(PacketTestCase):
	def setUp(self):
		super().setUp()
		self.a_full_operation()

	def test_it_files_a_governance_document_under_the_audit_packet_category(self):
		data = self.generate("GAP")
		row = STORE.get_raw("Governance Document", data["governance_document"])
		self.assertEqual(row["category"], "Audit Packet")
		self.assertEqual(row["company"], MAIN)

	def test_a_second_packet_for_the_same_period_is_refused(self):
		"""Two packets for one audit period, differing in whatever changed in
		between, is a question nobody wants to be asked."""
		self.generate("GAP")
		message = self.tool_error("generate_audit_packet", {"audit_type": "GAP", "company": MAIN, **PERIOD})
		self.assertIn("already filed", message)
		self.assertIn("overwrite=true", message)

	def test_overwrite_replaces_rather_than_filing_a_second(self):
		# Counted as a delta: the fixture site already has a governance archive,
		# which is the realistic case and the one an absolute count would hide.
		before = len(STORE.rows("Governance Document"))
		first = self.generate("GAP")
		second = self.generate("GAP", overwrite=True)
		self.assertEqual(first["governance_document"], second["governance_document"])
		self.assertTrue(second["replaced"])
		self.assertEqual(len(STORE.rows("Governance Document")), before + 1)

	def test_a_different_audit_type_over_the_same_period_is_a_different_packet(self):
		before = len(STORE.rows("Governance Document"))
		self.generate("GAP")
		self.generate("OSHA")
		self.assertEqual(len(STORE.rows("Governance Document")), before + 2)

	def test_the_bytes_are_not_returned_inline(self):
		"""A packet is measured in megabytes and a file_url in characters."""
		data = self.generate("GAP")
		self.assertNotIn("content", data["attachment"])
		self.assertTrue(data["attachment"]["file_url"])
		self.assertTrue(data["attachment"]["is_private"])

	def test_an_unknown_audit_type_is_refused_with_the_list(self):
		message = self.tool_error("generate_audit_packet", {"audit_type": "SOX", "company": MAIN, **PERIOD})
		self.assertIn("FSMA", message)
		self.assertIn("list_audit_packet_types", message)

	def test_a_backwards_period_is_refused(self):
		message = self.tool_error(
			"generate_audit_packet",
			{"audit_type": "GAP", "company": MAIN, "period_start": "2026-06-30", "period_end": "2026-01-01"},
		)
		self.assertIn("before", message)

	def test_a_bad_output_format_is_refused_and_says_why_pdf_is_the_default(self):
		message = self.tool_error(
			"generate_audit_packet",
			{"audit_type": "GAP", "company": MAIN, "output_format": "rtf", **PERIOD},
		)
		self.assertIn("may not be able to open", message)

	def test_it_is_scoped_to_one_company(self):
		self.generate("GAP")
		other = self.tool_data("generate_audit_packet", {"audit_type": "GAP", "company": OTHER, **PERIOD})
		self.assertEqual(other["packet"]["section_counts"]["policies"], 0)


class Staging(PacketTestCase):
	def setUp(self):
		super().setUp()
		self.a_full_operation()

	def test_a_small_packet_is_written_directly_and_says_why(self):
		"""Below the threshold the checkpoint costs more than the failure it
		guards against, and pretending otherwise would be ceremony."""
		staging = self.generate("GAP")["staging"]
		self.assertFalse(staging["staged"])
		self.assertIn("earns its keep", staging["reason"])

	def test_asking_for_staging_explicitly_routes_it_through_the_pipeline(self):
		staging = self.generate("GAP", stage_via_chunks=True)["staging"]
		self.assertTrue(staging["staged"])
		self.assertGreaterEqual(staging["chunks"], 1)
		self.assertEqual(len(staging["sha256"]), 64)

	def test_the_staging_session_is_cleared_once_the_file_exists(self):
		"""Staging that outlives the file it built is just rubbish on the site."""
		self.generate("GAP", stage_via_chunks=True)
		self.assertEqual(STORE.rows("Staged File Upload Session"), [])
		self.assertEqual(STORE.rows("Staged File Chunk"), [])

	def test_turning_it_off_skips_it_entirely(self):
		self.assertIsNone(self.generate("GAP", stage_via_chunks=False)["staging"])

	def test_a_round_trip_through_staging_produces_the_same_bytes(self):
		"""Read back OUT of staging rather than trusting what we still hold — a
		corrupted round trip is caught here and not in a PDF nobody can open."""
		from erpnext_mcp.tools import uploads

		content = b"%PDF-1.4 pretend document " * 500
		result = uploads.stage_internal_bytes(content, "test-session")
		self.assertEqual(result["content"], content)
		uploads.clear_internal_session(result["session"])


# ── what a section says when it is empty ────────────────────────────────────
class EmptySectionsExplainThemselves(PacketTestCase):
	"""An absent section reads as an operation with nothing to declare."""

	def test_traceability_is_empty_for_the_period_not_absent_by_app(self):
		"""Bucket Log Entry ships with erpnext_mcp since v0.44.0 — it cannot be
		"not installed" on a site running this app, so an FSMA packet with no
		bucket captures in the period says THAT, not that a bridge app is
		missing. See TraceabilityIsErpnextMcpsOwnDoctype for the populated case."""
		self.a_full_operation()
		section = next(
			entry for entry in self.generate("FSMA")["packet"]["sections"] if entry["key"] == "traceability"
		)
		self.assertEqual(section["row_count"], 0)
		self.assertIn("No records matched for this period", section["empty_note"])

	def test_spray_records_are_empty_for_the_period_not_absent_by_app(self):
		"""v0.90.0: Spray Application ships with erpnext_mcp, exactly as Bucket
		Log Entry does — it cannot be "not installed" on a site running this app.
		So an EPA packet with no applications in the period says THAT, and not
		that farm_precision_ag is missing. See SprayRecordsComeFromThisApp for
		the populated case."""
		self.a_full_operation()
		section = next(
			entry for entry in self.generate("EPA")["packet"]["sections"] if entry["key"] == "spray_records"
		)
		self.assertEqual(section["row_count"], 0)
		self.assertIn("No records matched for this period", section["empty_note"])
		self.assertNotIn("farm_precision_ag", section["empty_note"])

	def test_spray_records_name_the_doctype_when_neither_register_is_installed(self):
		"""The pre-migrate site, and the only case left in which this section is
		absent-by-app rather than empty-for-the-period. It names the DocType and
		the command that creates it, because "not installed" without a remedy is
		a dead end for the operator holding the packet."""
		self.a_full_operation()
		for doctype in ("Spray Application", "Spray Log"):
			INSTALLED_DOCTYPES.discard(doctype)
			self.addCleanup(INSTALLED_DOCTYPES.add, doctype)
		section = next(
			entry for entry in self.generate("EPA")["packet"]["sections"] if entry["key"] == "spray_records"
		)
		self.assertEqual(section["row_count"], 0)
		self.assertIn("Spray Application", section["empty_note"])
		self.assertIn("bench migrate", section["empty_note"])

	def test_workforce_says_the_compliance_fields_are_missing_rather_than_empty(self):
		"""ABSENT, not empty. An auditor should be told which."""
		install_hrms()
		self.a_full_operation()
		section = next(
			entry for entry in self.generate("DOL")["packet"]["sections"] if entry["key"] == "workforce"
		)
		self.assertIn("install_compliance_fields", section["empty_note"])
		self.assertIn("ABSENT rather than empty", section["empty_note"])

	def test_workforce_reports_the_i9_problems_it_finds_rather_than_filtering_them(self):
		"""An auditor who finds it themselves asks a much harder question."""
		install_hrms()
		for fieldname, fieldtype in (
			("i9_status", "Select"),
			("w4_status", "Select"),
			("jurisdiction", "Data"),
		):
			add_field("Employee", fieldname, fieldtype=fieldtype)
		row = STORE.get_raw("Employee", "HR-EMP-00001")
		row["i9_status"] = "Expired"
		row["company"] = MAIN
		self.a_full_operation()
		section = next(
			entry for entry in self.generate("DOL")["packet"]["sections"] if entry["key"] == "workforce"
		)
		self.assertIn("HR-EMP-00001", section["employees_without_a_verified_i9"])
		self.assertIn("filtered out of it", section["problem_note"])

	def test_every_empty_section_is_disclosed_at_the_top_of_the_packet(self):
		self.a_full_operation()
		disclosures = self.generate("FSMA")["disclosures"]
		self.assertTrue(any(entry["section"] == "traceability" for entry in disclosures))

	def test_the_type_listing_says_which_sections_will_be_empty_here(self):
		"""The negative control for the two tests below. Every DocType a packet
		reads ships with erpnext_mcp or with ERPNext, so on a healthy site the
		prediction is empty for every type — which would make an assertion that
		it PREDICTS nothing pass without the mechanism working at all. So this
		takes a DocType away and checks the prediction appears."""
		INSTALLED_DOCTYPES.discard("Certification")
		self.addCleanup(INSTALLED_DOCTYPES.add, "Certification")
		data = self.tool_data("list_audit_packet_types", {})
		gap = next(entry for entry in data["audit_types"] if entry["audit_type"] == "GAP")
		self.assertIn("certifications → Certification", gap["sections_that_will_be_empty_here"])

	def test_spray_records_is_never_listed_as_a_section_that_will_be_empty(self):
		"""v0.90.0. Spray Application ships with erpnext_mcp, so the five packet
		types that carry this section stopped predicting it empty. This is the
		regression that mattered: the prediction was read as "this farm cannot
		produce spray records", which was never true of a migrated site."""
		data = self.tool_data("list_audit_packet_types", {})
		for entry in data["audit_types"]:
			with self.subTest(audit_type=entry["audit_type"]):
				self.assertFalse(
					any("spray_records" in item for item in entry["sections_that_will_be_empty_here"]),
					entry["sections_that_will_be_empty_here"],
				)

	def test_bucket_log_entry_is_never_listed_as_a_section_that_will_be_empty(self):
		"""It ships with erpnext_mcp — it is always here, the same as Housing Unit
		and Field, neither of which is predicted empty either."""
		data = self.tool_data("list_audit_packet_types", {})
		fsma = next(entry for entry in data["audit_types"] if entry["audit_type"] == "FSMA")
		self.assertFalse(
			any("Bucket Log Entry" in entry for entry in fsma["sections_that_will_be_empty_here"])
		)


class SprayRecordsComeFromThisApp(PacketTestCase):
	"""v0.90.0: the spray section reads erpnext_mcp's OWN Spray Application.

	It read farm_precision_ag's Spray Log and nothing else, which meant five of
	the eight packet types announced up front that their spray records would be
	empty — on a site that had been recording every pass through
	`create_spray_application` since v0.79.0. The packet was telling an EPA
	inspector that this farm keeps no spray records while the register sat one
	tool call away.

	WHAT IS ASSERTED HERE IS THE JOIN, not the query: a pass is recorded through
	the real tool, and the block off the child table, the product and its
	registration number off the stored snapshot, and the applicator's NAME off
	the User all have to arrive on one row of the packet. Each of those is a
	different lookup and any one of them returning nothing would leave a column
	an inspector reads as a gap in the record.
	"""

	BLOCK = "Yellow Camp Block 3 - MC"
	CAPTAN = "CAPTAN-80WDG"

	def setUp(self):
		super().setUp()
		# Through the real installer: the interval columns are what
		# `stock_bridge.item_intervals` reads, and a fixture that added them by
		# hand would prove the copy works on a site this app never produces.
		compliance_fields.install_compliance_fields()
		STORE.seed("UOM", [{"name": "Lb", "enabled": 1}])
		STORE.seed(
			"Item",
			[
				{
					"name": self.CAPTAN,
					"item_code": self.CAPTAN,
					"item_name": "Captan 80 WDG",
					"stock_uom": "Lb",
					"is_stock_item": 1,
					"disabled": 0,
					"item_defaults": [],
					"reorder_levels": [],
					# The label numbers live on the Item, and the application
					# copies them at the moment of the pass. Passing them in the
					# `products` payload would be ignored — see
					# `spray._mix_products`, which reads `stock_bridge.item_intervals`.
					"rei_hours": 24,
					"phi_days": 14,
				}
			],
		)
		STORE.seed(
			"User",
			[{"name": "mendez@example.com", "email": "mendez@example.com", "full_name": "R. Mendez", "enabled": 1}],
		)

	def a_spray(self, **kw):
		payload = {
			"company": MAIN,
			"blocks": [{"block": self.BLOCK, "acres": 12.5}],
			"products": [
				{
					"item_code": self.CAPTAN,
					"rate_per_acre": 5,
					"rate_uom": "Lb",
					"epa_reg_number": "66222-242",
					"rei_hours": 24,
					"phi_days": 14,
				}
			],
			"applicator": "mendez@example.com",
			"applicator_license": "OR-PA-88213",
			"completed_at": "2026-04-05 11:30:00",
			"started_at": "2026-04-05 07:00:00",
			"wind_speed_mph": 4,
			"wind_direction": "WNW",
		}
		payload.update(kw)
		return self.tool_data("create_spray_application", payload)

	def spray_section(self, audit_type="EPA"):
		return next(
			entry
			for entry in self.generate(audit_type)["packet"]["sections"]
			if entry["key"] == "spray_records"
		)

	def test_an_application_in_the_period_is_in_the_packet(self):
		self.a_full_operation()
		created = self.a_spray()
		section = self.spray_section()
		self.assertEqual(section["row_count"], 1)
		row = section["rows"][0]
		self.assertEqual(row["spray"], created["name"])
		self.assertEqual(row["date"], "2026-04-05")
		self.assertEqual(row["source"], "Spray Application")

	def test_the_block_arrives_off_the_child_table(self):
		"""The block lives on Spray Application Block, one level down. A section
		that read only the parent would print a pesticide application over
		nowhere."""
		self.a_full_operation()
		self.a_spray()
		self.assertEqual(self.spray_section()["rows"][0]["block"], self.BLOCK)

	def test_the_product_and_its_registration_number_come_off_the_snapshot(self):
		"""Off `products_applied`, which is what went out on the day — not a live
		join to the Item, which would answer a question about today."""
		self.a_full_operation()
		self.a_spray()
		row = self.spray_section()["rows"][0]
		self.assertEqual(row["product"], "Captan 80 WDG")
		self.assertEqual(row["epa_reg_number"], "66222-242")
		self.assertEqual(row["rei_hours"], 24)
		self.assertEqual(row["phi_days"], 14)

	def test_the_applicator_is_named_rather_than_logged_in(self):
		"""A packet is handed to a person who wants to know who held the wand,
		and `mendez@example.com` is not an answer to that question."""
		self.a_full_operation()
		self.a_spray()
		row = self.spray_section()["rows"][0]
		self.assertEqual(row["applicator_name"], "R. Mendez")
		self.assertEqual(row["applicator_license"], "OR-PA-88213")

	def test_a_pass_outside_the_period_is_not_in_the_packet(self):
		self.a_full_operation()
		self.a_spray(completed_at="2026-08-05 11:30:00", started_at="2026-08-05 07:00:00")
		self.assertEqual(self.spray_section()["row_count"], 0)

	def test_a_pass_on_the_last_day_of_the_period_is_in_it(self):
		"""The classic off-by-a-day: `completed_at` is a Datetime and the period
		bound is a Date, so a spray finished at 11:30 on 30 June sorts AFTER
		'2026-06-30' unless the upper bound carries a time."""
		self.a_full_operation()
		self.a_spray(completed_at="2026-06-30 11:30:00", started_at="2026-06-30 07:00:00")
		self.assertEqual(self.spray_section()["row_count"], 1)

	def test_a_planned_pass_is_excluded_and_named(self):
		"""Nothing went on the ground, so it is not evidence — but a register
		compared against this packet has to reconcile, so it is named."""
		self.a_full_operation()
		created = self.a_spray(status="Planned")
		section = self.spray_section()
		self.assertEqual(section["row_count"], 0)
		self.assertIn(f"{created['name']} (Planned)", section["excluded_by_status"])
		self.assertIn("Planned or Cancelled", section["status_note"])

	def test_a_missing_registration_number_is_reported_not_hidden(self):
		self.a_full_operation()
		created = self.a_spray(
			products=[{"item_code": self.CAPTAN, "rate_per_acre": 5, "rate_uom": "Lb"}]
		)
		section = self.spray_section()
		self.assertEqual(section["rows"][0]["epa_reg_number"], "(unrecorded)")
		self.assertIn(created["name"], section["incomplete_records"])

	def test_the_fsma_packet_carries_it_too(self):
		"""Five types name this section. The bridge is in the section builder, so
		every one of them gets it — this is the one that proves it is not EPA
		special-casing."""
		self.a_full_operation()
		self.a_spray()
		self.assertEqual(self.spray_section("FSMA")["row_count"], 1)

	def test_the_packet_still_renders_with_a_spray_in_it(self):
		"""EveryAuditType's rule: a section builder that returns a shape the table
		emitter cannot draw is a packet discovered on the morning of the audit."""
		self.a_full_operation()
		self.a_spray()
		data = self.generate("EPA")
		self.assertGreater(data["attachment"]["file_size"], 1000)


class TraceabilityIsErpnextMcpsOwnDoctype(PacketTestCase):
	"""v0.44.0: Bucket Log Entry ships with erpnext_mcp, so a genuine capture in
	the period shows up in the FSMA packet's traceability section — reading the
	doctype's own `employee` and `verdict` fields rather than columns a
	third-party doctype might or might not carry."""

	def test_a_bucket_log_entry_in_the_period_appears_in_the_section(self):
		STORE.seed(
			"Bucket Log Entry",
			[
				{
					"name": "BLE-2026-0001",
					"entry_uuid": "11111111-1111-1111-1111-111111111111",
					"company": MAIN,
					"timestamp": "2026-03-15 09:00:00",
					"verdict": "Accepted",
					"employee": "HR-EMP-00001",
					"crew_id": "CREW-1",
					"block_id": "BLOCK-9",
					"bin_id": "BIN-4",
					"shipment_id": "",
					"status": "Pending",
				}
			],
		)
		self.a_full_operation()
		section = next(
			entry for entry in self.generate("FSMA")["packet"]["sections"] if entry["key"] == "traceability"
		)
		self.assertEqual(section["row_count"], 1)
		row = section["rows"][0]
		self.assertEqual(row["picker"], "HR-EMP-00001")
		self.assertEqual(row["crew_id"], "CREW-1")
		self.assertEqual(row["disposition"], "Accepted")
		# shipment_id was never set — the chain breaks there, and the packet
		# says so rather than silently dropping the row.
		self.assertEqual(row["shipment_id"], "(unlinked)")
		self.assertIn("BLE-2026-0001", section["chain_breaks"])


class TheEvidenceIsTheOperationalRecord(PacketTestCase):
	"""There is no shadow copy, and the packet says so.

	This is the Sprint 7 stance restated as an assertion: the spray records ARE
	the spray logs, the housing records ARE the housing register. A packet
	assembled from a compliance copy is one that can disagree with the records the
	auditor asks to see next.
	"""

	def setUp(self):
		super().setUp()
		self.a_full_operation()

	def test_the_housing_section_reads_the_housing_register_itself(self):
		section = next(
			entry for entry in self.generate("OSHA")["packet"]["sections"] if entry["key"] == "housing"
		)
		self.assertEqual(section["rows"][0]["unit"], "MC-Cabin-01 - MC")
		self.assertTrue(STORE.get_raw("Housing Unit", "MC-Cabin-01 - MC"))

	def test_a_superseded_policy_still_appears_for_the_period_it_governed(self):
		"""Presenting today's SOP as evidence about last July is the easiest way to
		be caught rewriting history."""
		self.configure(enabled=1, **ALL_ON, allow_supersede_compliance_policy=1)
		self.tool_data(
			"create_compliance_policy",
			{
				"policy_name": "Harvest Hygiene SOP 2027",
				"category": "Harvest Hygiene",
				"company": MAIN,
				"version": "v4",
				"effective_date": "2026-07-01",
			},
		)
		self.tool_data(
			"supersede_compliance_policy",
			{
				"policy": "Harvest Hygiene SOP",
				"superseded_by": "Harvest Hygiene SOP 2027",
				"reason": "annual revision after the 2026 PrimusGFS finding",
			},
		)
		section = next(
			entry for entry in self.generate("GAP")["packet"]["sections"] if entry["key"] == "policies"
		)
		names = [row["policy"] for row in section["rows"]]
		self.assertIn("Harvest Hygiene SOP", names)

	def test_a_draft_policy_is_never_produced_as_a_procedure_in_force(self):
		self.tool_data(
			"create_compliance_policy",
			{
				"policy_name": "Unadopted SOP",
				"category": "Harvest Hygiene",
				"company": MAIN,
				"status": "Draft",
				"effective_date": "2026-01-01",
			},
		)
		section = next(
			entry for entry in self.generate("GAP")["packet"]["sections"] if entry["key"] == "policies"
		)
		self.assertNotIn("Unadopted SOP", [row["policy"] for row in section["rows"]])

	def test_a_certificate_that_lapsed_mid_period_is_shown_rather_than_filtered(self):
		"""A gap in coverage, shown rather than hidden — an auditor who finds it
		themselves asks a harder question."""
		self.tool_data(
			"create_certification",
			{
				"cert_name": "Lapsed Cert",
				"cert_type": "PrimusGFS",
				"company": MAIN,
				"issued_date": "2025-01-01",
				"expiration_date": "2026-03-01",
			},
		)
		section = next(
			entry for entry in self.generate("GAP")["packet"]["sections"] if entry["key"] == "certifications"
		)
		self.assertIn("Lapsed Cert", section["coverage_gaps"])

	def test_a_certificate_that_expired_before_the_period_is_not_evidence_about_it(self):
		self.tool_data(
			"create_certification",
			{
				"cert_name": "Ancient Cert",
				"cert_type": "PrimusGFS",
				"company": MAIN,
				"issued_date": "2023-01-01",
				"expiration_date": "2024-01-01",
			},
		)
		section = next(
			entry for entry in self.generate("GAP")["packet"]["sections"] if entry["key"] == "certifications"
		)
		self.assertNotIn("Ancient Cert", [row["certificate"] for row in section["rows"]])

	def test_a_draft_filing_is_never_produced_as_something_that_was_sent(self):
		self.tool_data(
			"create_regulatory_filing",
			{
				"filing_name": "Never Sent",
				"agency": "OSHA",
				"filing_type": "OSHA-300A",
				"company": MAIN,
				"status": "Draft",
			},
		)
		section = next(
			entry for entry in self.generate("OSHA")["packet"]["sections"] if entry["key"] == "filings"
		)
		self.assertNotIn("Never Sent", [row["filing"] for row in section["rows"]])

	def test_the_water_section_applies_the_ninety_day_window_to_the_PERIOD_END(self):
		"""Not to today, which would flatter a packet about last season."""
		section = next(
			entry for entry in self.generate("FSMA")["packet"]["sections"] if entry["key"] == "water"
		)
		block = next(row for row in section["rows"] if row["block"].startswith("Yellow Camp"))
		self.assertTrue(block["current_at_period_end"])
		self.assertTrue(block["tested_within_period"])

	def test_the_provenance_paragraph_says_there_is_no_shadow_copy(self):
		packet = self.generate("GAP")["packet"]
		text = " ".join(
			str(payload) for kind, payload in audit_packets.document_sections(packet) if kind == "paragraph"
		)
		self.assertIn("ARE the spray logs", text)
		self.assertIn("can have drifted", text)


# ── the Compliance Command Center ───────────────────────────────────────────
class TheCommandCenter(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1)

	def test_it_builds_the_dashboard_its_cards_and_its_charts(self):
		report = dashboard.install_command_center()
		self.assertEqual(len(report["created_cards"]), len(dashboard.CARDS))
		self.assertEqual(len(report["created_charts"]), len(dashboard.CHARTS))
		self.assertTrue(report.get("dashboard_created"))
		self.assertEqual(report["failed"], [])

	def test_the_landing_page_is_the_route_frappe_derives_from_the_name(self):
		report = dashboard.install_command_center()
		self.assertEqual(report["route"], "/app/compliance-command-center")
		self.assertTrue(STORE.get_raw("Dashboard", dashboard.DASHBOARD_NAME))

	def test_three_migrations_build_it_once(self):
		"""It is an INSTALLER, not a fixture. A fixture cannot look at what is
		already there, so an operator who reordered their cards would get it
		silently put back on every migrate."""
		counts = []
		for _ in range(3):
			install.after_migrate()
			counts.append((len(STORE.rows("Number Card")), len(STORE.rows("Dashboard Chart"))))
		self.assertEqual(counts, [counts[0]] * 3, f"the dashboard multiplied across migrations: {counts}")

	def test_a_card_an_operator_edited_is_left_alone(self):
		dashboard.install_command_center()
		card = STORE.get_raw("Number Card", "Critical Compliance Alerts")
		card["color"] = "#000000"
		dashboard.install_command_center()
		self.assertEqual(STORE.get_raw("Number Card", "Critical Compliance Alerts")["color"], "#000000")

	def test_a_dashboard_an_operator_stripped_is_not_rebuilt(self):
		"""Somebody who took a chart off took it off on purpose."""
		dashboard.install_command_center()
		STORE.get_raw("Dashboard", dashboard.DASHBOARD_NAME)["charts"] = []
		report = dashboard.install_command_center()
		self.assertTrue(report["dashboard_existed"])
		self.assertEqual(STORE.get_raw("Dashboard", dashboard.DASHBOARD_NAME)["charts"], [])

	def test_it_degrades_on_a_site_without_the_dashboard_doctypes(self):
		"""A dashboard that cannot be built is not a reason for a migration to
		fail, and every underlying number stays readable."""
		for doctype in ("Dashboard", "Dashboard Chart", "Number Card"):
			INSTALLED_DOCTYPES.discard(doctype)
		try:
			report = dashboard.install_command_center()
			self.assertFalse(report["available"])
			self.assertIn("get_compliance_calendar", report["note"])
		finally:
			for doctype in ("Dashboard", "Dashboard Chart", "Number Card"):
				INSTALLED_DOCTYPES.add(doctype)

	def test_it_waits_for_the_alert_doctype_rather_than_building_a_broken_page(self):
		INSTALLED_DOCTYPES.discard("Compliance Alert")
		try:
			report = dashboard.install_command_center()
			self.assertFalse(report["available"])
			self.assertIn("bench migrate", report["note"])
			self.assertEqual(STORE.rows("Number Card"), [])
		finally:
			INSTALLED_DOCTYPES.add("Compliance Alert")

	def test_every_card_and_chart_says_what_it_is_for(self):
		"""A number on a wall that nobody can explain is a number nobody acts on."""
		for spec in dashboard.CARDS + dashboard.CHARTS:
			with self.subTest(name=spec.get("label") or spec.get("chart_name")):
				self.assertGreater(len(spec["why"]), 40)

	def test_the_cards_read_live_alerts_and_not_dismissed_ones(self):
		for spec in dashboard.CARDS:
			if spec["document_type"] != dashboard.ALERT_DOCTYPE:
				continue
			with self.subTest(card=spec["label"]):
				clauses = json.loads(spec["filters_json"])
				self.assertIn([dashboard.ALERT_DOCTYPE, "dismissed", "=", 0], clauses)

	# ── v0.18.5: the shape a Number Card can actually be counted from ────────
	def test_every_card_and_chart_filter_is_a_list_and_never_a_dict(self):
		"""The v0.18.4 bug, asserted at the point it actually broke.

		`number_card.get_result` appends a date clause to whatever `filters_json`
		parses to, so a dict — which Frappe accepts everywhere else a filter is
		read — raises `TypeError: 'NoneType' object is not callable` and takes the
		whole workspace's render with it. A dict is a valid filter to query WITH
		and an invalid one to build ON.
		"""
		specs = (
			dashboard.CARDS + dashboard.CHARTS + dashboard.DISPATCH_NUMBER_CARDS + dashboard.DISPATCH_CHARTS
		)
		for spec in specs:
			name = spec.get("label") or spec.get("chart_name")
			with self.subTest(name=name):
				clauses = json.loads(spec["filters_json"])
				self.assertIsInstance(clauses, list, f"{name} filters_json is a {type(clauses).__name__}")
				for clause in clauses:
					self.assertIsInstance(clause, list, f"{name} has a non-list clause {clause!r}")
					self.assertEqual(
						len(clause), 4, f"{name} clause {clause!r} is not [doctype, field, op, value]"
					)
					self.assertEqual(
						clause[0],
						spec["document_type"],
						f"{name} clause {clause!r} names a doctype the card does not count",
					)

	def test_a_card_left_over_from_a_previous_release_has_its_filters_repaired(self):
		"""Fixing the spec fixes new sites. Every site that already ran the old
		one still holds the broken card, and `_build` leaves it alone forever."""
		dashboard.install_command_center()
		card = STORE.get_raw("Number Card", "Critical Compliance Alerts")
		card["filters_json"] = json.dumps({"dismissed": 0, "severity": "Critical"})

		report = dashboard.install_command_center()
		self.assertIn("Critical Compliance Alerts", report["repaired_filters"])
		repaired = json.loads(STORE.get_raw("Number Card", "Critical Compliance Alerts")["filters_json"])
		self.assertEqual(
			sorted(repaired),
			sorted(
				[
					[dashboard.ALERT_DOCTYPE, "dismissed", "=", 0],
					[dashboard.ALERT_DOCTYPE, "severity", "=", "Critical"],
				]
			),
		)

	def test_the_repair_carries_an_operators_own_clauses_across_rather_than_replacing_them(self):
		"""Somebody who changed what a card counts changed it on purpose. The
		repair makes their card work; it does not make it this app's card."""
		dashboard.install_command_center()
		card = STORE.get_raw("Number Card", "Warning Compliance Alerts")
		card["filters_json"] = json.dumps({"severity": "Info", "category": "Housing"})

		dashboard.install_command_center()
		repaired = json.loads(STORE.get_raw("Number Card", "Warning Compliance Alerts")["filters_json"])
		self.assertIn([dashboard.ALERT_DOCTYPE, "severity", "=", "Info"], repaired)
		self.assertIn([dashboard.ALERT_DOCTYPE, "category", "=", "Housing"], repaired)
		self.assertNotIn([dashboard.ALERT_DOCTYPE, "severity", "=", "Warning"], repaired)

	def test_a_card_already_in_the_list_shape_is_not_touched(self):
		"""The repair is narrow or it is a fixture wearing a bug fix as a coat."""
		dashboard.install_command_center()
		card = STORE.get_raw("Number Card", "Info Compliance Alerts")
		mine = json.dumps([[dashboard.ALERT_DOCTYPE, "severity", "=", "Critical"]])
		card["filters_json"] = mine

		report = dashboard.install_command_center()
		self.assertNotIn("Info Compliance Alerts", report["repaired_filters"])
		self.assertEqual(STORE.get_raw("Number Card", "Info Compliance Alerts")["filters_json"], mine)

	def test_a_fresh_install_needs_no_repairs_at_all(self):
		"""If the specs are right, the repair clause is dead code on a new site."""
		report = dashboard.install_command_center()
		self.assertEqual(report["repaired_filters"], [])

	def test_the_readiness_score_is_computed_rather_than_carded(self):
		"""A Number Card can count one collection; a ratio of two needs code."""
		self.assertIsInstance(dashboard.readiness()["audit_readiness_score"], float)
