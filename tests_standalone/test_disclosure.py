# SPDX-License-Identifier: MIT
"""Phase 6 — reporting templates, the MD&A feed, segments, and disclosure checklists.

THE CLASS THAT CARRIES THE ARGUMENT IS `NotApplicableIsADecisionAndOutstandingIsNot`.
The whole value of a disclosure checklist is that somebody decided about every
line; "we have no reportable segments" is a decision and an empty row is an
omission, and a system that let those look alike would produce a checklist that
reads as finished and is not.

`AFeedIsHonestAboutItsHoles` checks the other load-bearing claim. A generator
that raised on its first missing input would be a tool nobody on a farm mid-setup
could ever run, so every source is attempted and every failure is named. The test
asserts the naming, because a silent hole is the dangerous kind.
"""

import frappe

from erpnext_mcp import compliance_rules
from erpnext_mcp.tools import disclosure

from .fixtures import MAIN, MAIN_ABBR, SeededTestCase
from .harness import STORE

ALL_ON = {
	"allow_create_reporting_template": 1,
	"allow_get_reporting_template": 1,
	"allow_list_reporting_templates": 1,
	"allow_update_reporting_template": 1,
	"allow_generate_mda_data_feed": 1,
	"allow_generate_segment_report": 1,
	"allow_create_disclosure_checklist": 1,
	"allow_get_disclosure_checklist": 1,
	"allow_list_disclosure_checklists": 1,
	"allow_update_disclosure_checklist": 1,
	"allow_complete_disclosure_item": 1,
	"allow_generate_quarterly_report_skeleton": 1,
}

QUARTER_START = "2026-04-01"
QUARTER_END = "2026-06-30"


class DisclosureTestCase(SeededTestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)
		frappe.local.session.user = "Administrator"
		compliance_rules.seed_compliance_rules()
		# `install_reporting_templates` is what `bench migrate` runs; the three
		# shipped shapes are part of the site a real operator has.
		disclosure.install_reporting_templates()

	def a_template(self, **extra) -> dict:
		payload = {
			"company": MAIN,
			"template_name": "Lender Quarterly Package",
			"report_type": "Quarterly",
			"sections": [
				{"section_name": "Results of Operations", "data_source": "compute_all_kpis"},
				{"section_name": "Liquidity", "data_source": "get_cash_flow_summary", "label_es": "Liquidez"},
				{"section_name": "Outlook", "required": False},
			],
			**extra,
		}
		return self.tool_data("create_reporting_template", payload)

	def a_checklist(self, **extra) -> dict:
		payload = {
			"company": MAIN,
			"filing_type": "10-Q",
			"period_start": QUARTER_START,
			"period_end": QUARTER_END,
			"items": [
				{"disclosure_item": "Related party transactions", "requirement_reference": "ASC 850"},
				{"disclosure_item": "Subsequent events", "requirement_reference": "ASC 855"},
				{"disclosure_item": "Segment information", "requirement_reference": "ASC 280", "required": False},
			],
			**extra,
		}
		return self.tool_data("create_disclosure_checklist", payload)

	def a_gl_row(self, name, account, cost_center, debit=0.0, credit=0.0, posting_date="2026-05-15") -> dict:
		return {
			"name": name,
			"company": MAIN,
			"account": account,
			"cost_center": cost_center,
			"posting_date": posting_date,
			"debit": float(debit),
			"credit": float(credit),
			"is_cancelled": 0,
			"is_opening": "No",
		}

	def seed_segments(self) -> None:
		"""Two cost centres with income and expense, plus one unassigned posting."""
		income = f"Sales - {MAIN_ABBR}"
		expense = f"Cost of Goods Sold - {MAIN_ABBR}"
		STORE.seed(
			"Account",
			[
				{"name": income, "company": MAIN, "root_type": "Income", "account_type": "Income Account"},
				{"name": expense, "company": MAIN, "root_type": "Expense", "account_type": "Expense Account"},
			],
		)
		STORE.seed(
			"Cost Center",
			[
				{"name": f"Orchard - {MAIN_ABBR}", "company": MAIN, "is_group": 0},
				{"name": f"Packing - {MAIN_ABBR}", "company": MAIN, "is_group": 0},
			],
		)
		STORE.seed(
			"GL Entry",
			[
				self.a_gl_row("GL-S1", income, f"Orchard - {MAIN_ABBR}", credit=900000),
				self.a_gl_row("GL-C1", expense, f"Orchard - {MAIN_ABBR}", debit=600000),
				self.a_gl_row("GL-S2", income, f"Packing - {MAIN_ABBR}", credit=100000),
				self.a_gl_row("GL-C2", expense, f"Packing - {MAIN_ABBR}", debit=40000),
				self.a_gl_row("GL-S3", income, None, credit=5000),
			],
		)

	def rule_for(self, control_point: str) -> str:
		name = frappe.db.get_value("Compliance Rule", {"control_point": control_point}, "name")
		self.assertTrue(name, f"no Compliance Rule seeded for control point {control_point}")
		return name

	def set_mode(self, control_point: str, mode: str) -> None:
		frappe.db.set_value("Compliance Rule", self.rule_for(control_point), "enforcement_mode", mode)


# ── 1 · reporting templates ─────────────────────────────────────────────────
class TheShapeOfAReport(DisclosureTestCase):
	def test_a_template_carries_its_company_in_its_docname(self):
		data = self.a_template()
		self.assertEqual(data["name"], f"Lender Quarterly Package - {MAIN_ABBR}")

	def test_sections_keep_their_order_and_their_sources(self):
		data = self.a_template()
		self.assertEqual([row["section_name"] for row in data["sections"]], ["Results of Operations", "Liquidity", "Outlook"])
		self.assertEqual(data["sections"][0]["data_source"], "compute_all_kpis")

	def test_a_template_with_no_sections_is_refused(self):
		message = self.tool_error(
			"create_reporting_template",
			{"company": MAIN, "template_name": "Empty", "report_type": "Quarterly", "sections": []},
		)
		self.assertIn("not a shape", message)

	def test_two_sections_with_one_name_are_refused(self):
		message = self.tool_error(
			"create_reporting_template",
			{
				"company": MAIN,
				"template_name": "Doubled",
				"report_type": "Quarterly",
				"sections": ["Liquidity", "liquidity"],
			},
		)
		self.assertIn("silently become one", message)

	def test_a_second_template_of_the_same_name_is_refused(self):
		self.a_template()
		message = self.tool_error(
			"create_reporting_template",
			{
				"company": MAIN,
				"template_name": "Lender Quarterly Package",
				"report_type": "Quarterly",
				"sections": ["Overview"],
			},
		)
		self.assertIn("already exists", message)

	def test_a_missing_spanish_label_is_reported_rather_than_hidden(self):
		self.a_template()
		data = self.tool_data(
			"get_reporting_template",
			{"reporting_template": f"Lender Quarterly Package - {MAIN_ABBR}", "language": "es"},
		)
		self.assertIn("untranslated", data)
		self.assertIn("nobody finds out", data["language_note"])

	def test_a_present_spanish_label_is_served(self):
		self.a_template()
		data = self.tool_data(
			"get_reporting_template",
			{"reporting_template": f"Lender Quarterly Package - {MAIN_ABBR}", "language": "es"},
		)
		liquidity = next(row for row in data["sections"] if row["section_name"] == "Liquidity")
		self.assertEqual(liquidity["label"], "Liquidez")

	def test_sections_are_restated_whole_rather_than_merged(self):
		self.a_template()
		data = self.tool_data(
			"update_reporting_template",
			{
				"reporting_template": f"Lender Quarterly Package - {MAIN_ABBR}",
				"sections": ["Overview", "Results of Operations"],
			},
		)
		self.assertEqual([row["section_name"] for row in data["sections"]], ["Overview", "Results of Operations"])
		self.assertIn("restated whole", data["sections_note"])

	def test_emptying_the_sections_is_refused_and_disabling_is_offered(self):
		self.a_template()
		message = self.tool_error(
			"update_reporting_template",
			{"reporting_template": f"Lender Quarterly Package - {MAIN_ABBR}", "sections": []},
		)
		self.assertIn("enabled=false", message)

	def test_a_disabled_template_is_hidden_from_the_list(self):
		self.a_template()
		self.tool_data(
			"update_reporting_template",
			{"reporting_template": f"Lender Quarterly Package - {MAIN_ABBR}", "enabled": False},
		)
		listed = self.tool_data("list_reporting_templates", {"company": MAIN})
		self.assertNotIn(
			f"Lender Quarterly Package - {MAIN_ABBR}", {row["name"] for row in listed["reporting_templates"]}
		)

	def test_the_three_shipped_shapes_are_seeded(self):
		listed = self.tool_data("list_reporting_templates", {"company": MAIN})
		names = {row["template_name"] for row in listed["reporting_templates"]}
		self.assertLessEqual({"10-K Sections", "10-Q Sections", "MD&A"}, names)


# ── 2 · segments ────────────────────────────────────────────────────────────
class SegmentsAreAppliedAndShownNotDecided(DisclosureTestCase):
	def test_it_cuts_revenue_and_expense_by_cost_centre(self):
		self.seed_segments()
		data = self.tool_data(
			"generate_segment_report", {"company": MAIN, "from_date": QUARTER_START, "to_date": QUARTER_END}
		)
		orchard = next(row for row in data["segments"] if row["segment"] == f"Orchard - {MAIN_ABBR}")
		self.assertEqual(orchard["revenue"], 900000.0)
		self.assertEqual(orchard["expense"], 600000.0)
		self.assertEqual(orchard["result"], 300000.0)

	def test_the_ten_percent_test_is_applied_with_its_working_shown(self):
		self.seed_segments()
		data = self.tool_data(
			"generate_segment_report", {"company": MAIN, "from_date": QUARTER_START, "to_date": QUARTER_END}
		)
		orchard = next(row for row in data["segments"] if row["segment"] == f"Orchard - {MAIN_ABBR}")
		self.assertTrue(orchard["reportable"])
		self.assertTrue(any("% of combined revenue" in reason for reason in orchard["reportable_because"]))

	def test_it_refuses_to_call_the_judgement(self):
		self.seed_segments()
		data = self.tool_data(
			"generate_segment_report", {"company": MAIN, "from_date": QUARTER_START, "to_date": QUARTER_END}
		)
		self.assertIn("applied, NOT decided", data["reportability_note"])

	def test_postings_with_no_cost_centre_are_reported_separately(self):
		self.seed_segments()
		data = self.tool_data(
			"generate_segment_report", {"company": MAIN, "from_date": QUARTER_START, "to_date": QUARTER_END}
		)
		self.assertEqual(data["unassigned"]["revenue"], 5000.0)
		self.assertIn("describing part of the business", data["unassigned"]["why_it_matters"])

	def test_the_seventy_five_percent_coverage_test_is_reported(self):
		self.seed_segments()
		data = self.tool_data(
			"generate_segment_report", {"company": MAIN, "from_date": QUARTER_START, "to_date": QUARTER_END}
		)
		self.assertIn("meets_75_percent_test", data)

	def test_an_empty_window_says_so_rather_than_reporting_no_segments(self):
		data = self.tool_data(
			"generate_segment_report", {"company": MAIN, "from_date": "2019-01-01", "to_date": "2019-03-31"}
		)
		self.assertIn("empty window", data["empty_note"])


# ── 3 · the MD&A feed ───────────────────────────────────────────────────────
class AFeedIsHonestAboutItsHoles(DisclosureTestCase):
	def test_it_returns_a_feed_and_names_what_it_could_not_read(self):
		data = self.tool_data(
			"generate_mda_data_feed", {"company": MAIN, "from_date": QUARTER_START, "to_date": QUARTER_END}
		)
		self.assertIn("feed", data)
		self.assertIn("unavailable", data)
		self.assertEqual(data["complete"], not data["unavailable"])

	def test_a_missing_source_is_named_with_its_reason(self):
		data = self.tool_data(
			"generate_mda_data_feed", {"company": MAIN, "from_date": QUARTER_START, "to_date": QUARTER_END}
		)
		for entry in data["unavailable"]:
			with self.subTest(source=entry["source"]):
				self.assertTrue(entry["reason"])
				self.assertIn(entry["kind"], ("refused", "error"))

	def test_it_never_raises_because_one_source_is_missing(self):
		"""A generator that raised on the first hole would never be run on a real farm."""
		data = self.tool_data("generate_mda_data_feed", {"company": MAIN})
		self.assertIsInstance(data["sources_read"], list)

	def test_it_says_it_is_evidence_rather_than_a_draft(self):
		data = self.tool_data("generate_mda_data_feed", {"company": MAIN})
		self.assertIn("written FROM, not a draft", data["what_this_is"])

	def test_a_window_the_wrong_way_round_is_refused(self):
		message = self.tool_error(
			"generate_mda_data_feed", {"company": MAIN, "from_date": "2026-12-31", "to_date": "2026-01-01"}
		)
		self.assertIn("is after", message)


# ── 4 · the checklist ───────────────────────────────────────────────────────
class NotApplicableIsADecisionAndOutstandingIsNot(DisclosureTestCase):
	def test_a_new_checklist_counts_every_required_item_as_outstanding(self):
		data = self.a_checklist()
		self.assertEqual(data["required_count"], 2)
		self.assertEqual(len(data["outstanding_required"]), 2)
		self.assertFalse(data["complete"])

	def test_completing_an_item_settles_it(self):
		checklist = self.a_checklist()
		data = self.tool_data(
			"complete_disclosure_item",
			{
				"disclosure_checklist": checklist["name"],
				"disclosure_item": "Related party transactions",
				"evidence_reference": "generate_related_party_disclosure, working paper RP-1",
			},
		)
		self.assertEqual(data["status_change"], "Outstanding → Complete")
		self.assertNotIn("Related party transactions", data["outstanding_required"])

	def test_not_applicable_settles_it_too(self):
		checklist = self.a_checklist()
		data = self.tool_data(
			"complete_disclosure_item",
			{
				"disclosure_checklist": checklist["name"],
				"disclosure_item": "Subsequent events",
				"status": "Not Applicable",
				"notes": "Nothing occurred between the period end and this filing.",
			},
		)
		self.assertNotIn("Subsequent events", data["outstanding_required"])
		self.assertEqual(data["not_applicable_count"], 1)

	def test_not_applicable_without_a_reason_is_refused(self):
		"""The reason IS the disclosure."""
		checklist = self.a_checklist()
		message = self.tool_error(
			"complete_disclosure_item",
			{
				"disclosure_checklist": checklist["name"],
				"disclosure_item": "Subsequent events",
				"status": "Not Applicable",
			},
		)
		self.assertIn("reason IS the disclosure", message)
		self.assertIn("Nothing was changed", message)

	def test_in_progress_is_not_settled(self):
		checklist = self.a_checklist()
		data = self.tool_data(
			"complete_disclosure_item",
			{
				"disclosure_checklist": checklist["name"],
				"disclosure_item": "Subsequent events",
				"status": "In Progress",
			},
		)
		self.assertIn("Subsequent events", data["outstanding_required"])

	def test_completing_without_evidence_is_allowed_but_named(self):
		checklist = self.a_checklist()
		data = self.tool_data(
			"complete_disclosure_item",
			{"disclosure_checklist": checklist["name"], "disclosure_item": "Related party transactions"},
		)
		self.assertIn("points at nothing is a tick", data["note"])

	def test_an_item_that_is_not_on_the_checklist_is_refused_with_the_list(self):
		checklist = self.a_checklist()
		message = self.tool_error(
			"complete_disclosure_item",
			{"disclosure_checklist": checklist["name"], "disclosure_item": "Something else"},
		)
		self.assertIn("is not an item", message)
		self.assertIn("Subsequent events", message)

	def test_two_items_with_one_name_are_refused(self):
		message = self.tool_error(
			"create_disclosure_checklist",
			{
				"company": MAIN,
				"filing_type": "10-Q",
				"period_start": QUARTER_START,
				"period_end": QUARTER_END,
				"items": ["Segment information", "segment information"],
			},
		)
		self.assertIn("ambiguous", message)

	def test_reopening_an_item_clears_who_completed_it(self):
		checklist = self.a_checklist()
		self.tool_data(
			"complete_disclosure_item",
			{"disclosure_checklist": checklist["name"], "disclosure_item": "Related party transactions"},
		)
		data = self.tool_data(
			"complete_disclosure_item",
			{
				"disclosure_checklist": checklist["name"],
				"disclosure_item": "Related party transactions",
				"status": "Outstanding",
			},
		)
		row = next(item for item in data["items"] if item["disclosure_item"] == "Related party transactions")
		self.assertIsNone(row["completed_by"])

	def test_an_unassigned_outstanding_item_is_named(self):
		data = self.a_checklist()
		self.assertIn("nobody owed it", data["next_step"])


class TheCompletenessGate(DisclosureTestCase):
	def test_filing_with_items_outstanding_is_reported_and_allowed(self):
		checklist = self.a_checklist()
		data = self.tool_data(
			"update_disclosure_checklist", {"disclosure_checklist": checklist["name"], "status": "Filed"}
		)
		self.assertEqual(data["status"], "Filed")
		self.assertEqual(data["control"]["action"], "reported")

	def test_filing_with_items_outstanding_is_refused_under_enforcement(self):
		checklist = self.a_checklist()
		self.set_mode("disclosure_completeness", "Enforced")
		message = self.tool_error(
			"update_disclosure_checklist", {"disclosure_checklist": checklist["name"], "status": "Filed"}
		)
		self.assertIn("REFUSED", message)
		self.assertIn("complete_disclosure_item", message)
		self.assertEqual(frappe.db.get_value("Disclosure Checklist", checklist["name"], "status"), "Open")

	def test_a_settled_checklist_files_cleanly_under_enforcement(self):
		checklist = self.a_checklist()
		for item, payload in (
			("Related party transactions", {}),
			("Subsequent events", {"status": "Not Applicable", "notes": "Nothing occurred."}),
		):
			self.tool_data(
				"complete_disclosure_item",
				{"disclosure_checklist": checklist["name"], "disclosure_item": item, **payload},
			)
		self.set_mode("disclosure_completeness", "Enforced")
		data = self.tool_data(
			"update_disclosure_checklist", {"disclosure_checklist": checklist["name"], "status": "Filed"}
		)
		self.assertEqual(data["status"], "Filed")
		self.assertTrue(data["complete"])

	def test_an_optional_item_left_outstanding_does_not_block(self):
		checklist = self.a_checklist()
		for item, payload in (
			("Related party transactions", {}),
			("Subsequent events", {"status": "Not Applicable", "notes": "Nothing occurred."}),
		):
			self.tool_data(
				"complete_disclosure_item",
				{"disclosure_checklist": checklist["name"], "disclosure_item": item, **payload},
			)
		self.set_mode("disclosure_completeness", "Enforced")
		self.tool_data("update_disclosure_checklist", {"disclosure_checklist": checklist["name"], "status": "Filed"})
		data = self.tool_data("get_disclosure_checklist", {"disclosure_checklist": checklist["name"]})
		self.assertEqual(data["status"], "Filed")

	def test_the_read_shows_what_enforcement_would_do_without_doing_it(self):
		checklist = self.a_checklist()
		self.set_mode("disclosure_completeness", "Enforced")
		data = self.tool_data("get_disclosure_checklist", {"disclosure_checklist": checklist["name"]})
		self.assertTrue(data["control"]["enforced"])
		self.assertEqual(data["control"]["finding_count"], 1)

	def test_a_filing_marked_filed_while_incomplete_is_listed(self):
		checklist = self.a_checklist()
		self.tool_data("update_disclosure_checklist", {"disclosure_checklist": checklist["name"], "status": "Filed"})
		data = self.tool_data("list_disclosure_checklists", {"company": MAIN})
		self.assertIn(checklist["name"], data["filed_incomplete"])
		self.assertIn("before deciding whether to enforce", data["filed_incomplete_note"])

	def test_the_control_ships_advisory(self):
		mode = frappe.db.get_value(
			"Compliance Rule", self.rule_for("disclosure_completeness"), "enforcement_mode"
		)
		self.assertEqual(mode, "Advisory")


# ── 5 · the skeleton ────────────────────────────────────────────────────────
class TheSkeletonIsASkeleton(DisclosureTestCase):
	def test_it_names_its_sections_and_their_sources(self):
		self.a_template()
		data = self.tool_data(
			"generate_quarterly_report_skeleton",
			{
				"company": MAIN,
				"from_date": QUARTER_START,
				"to_date": QUARTER_END,
				"reporting_template": f"Lender Quarterly Package - {MAIN_ABBR}",
			},
		)
		self.assertEqual(data["section_count"], 3)
		self.assertEqual(data["sections"][0]["data_source"], "compute_all_kpis")

	def test_a_section_with_no_runnable_source_is_marked_for_a_person(self):
		self.a_template()
		data = self.tool_data(
			"generate_quarterly_report_skeleton",
			{
				"company": MAIN,
				"from_date": QUARTER_START,
				"to_date": QUARTER_END,
				"reporting_template": f"Lender Quarterly Package - {MAIN_ABBR}",
			},
		)
		self.assertIn("Outlook", data["sections_needing_a_writer"])

	def test_it_contains_no_prose_and_says_so(self):
		data = self.tool_data("generate_quarterly_report_skeleton", {"company": MAIN})
		self.assertIn("contains no prose and never will", data["what_this_is"])

	def test_it_picks_the_quarterly_template_when_none_is_named(self):
		data = self.tool_data(
			"generate_quarterly_report_skeleton",
			{"company": MAIN, "from_date": QUARTER_START, "to_date": QUARTER_END},
		)
		self.assertTrue(data["reporting_template"])

	def test_it_reports_a_missing_checklist_as_a_gap(self):
		data = self.tool_data("generate_quarterly_report_skeleton", {"company": MAIN})
		self.assertTrue(any("No disclosure checklist" in gap for gap in data["gaps"]))
		self.assertFalse(data["ready"])

	def test_a_named_checklist_carries_its_outstanding_items_into_the_skeleton(self):
		checklist = self.a_checklist()
		data = self.tool_data(
			"generate_quarterly_report_skeleton",
			{
				"company": MAIN,
				"from_date": QUARTER_START,
				"to_date": QUARTER_END,
				"disclosure_checklist": checklist["name"],
			},
		)
		self.assertEqual(len(data["disclosure_checklist"]["outstanding_required"]), 2)
		self.assertTrue(any("required disclosure(s) are outstanding" in gap for gap in data["gaps"]))

	def test_it_stores_nothing(self):
		before = len(STORE.rows("Disclosure Checklist"))
		self.tool_data("generate_quarterly_report_skeleton", {"company": MAIN})
		self.assertEqual(len(STORE.rows("Disclosure Checklist")), before)
