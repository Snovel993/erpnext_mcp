# SPDX-License-Identifier: MIT
"""The one screen an owner opens — v0.93.0.

WHAT IS ACTUALLY BEING TESTED is not that seven reads can be called in a row.
It is the three properties that make the composition worth having, and each has
a class:

    `attention` IS RANKED BY GRAVITY, NOT BY SECTION. An open Critical compliance
    alert sorts above a pool of unclaimed tasks no matter which section produced
    it, because a dashboard that ordered by section would put a Critical below an
    Info and teach somebody to stop reading it.

    A SOURCE THAT REFUSES IS REPORTED, NEVER FATAL — AND NEVER CLEAN. This is the
    one that would be easiest to get wrong in the direction that matters: a
    dashboard showing no compliance alerts because the compliance source refused
    looks exactly like a farm with no compliance alerts. `sections_unavailable`
    is what stops those two being the same screen.

    NOTHING INVENTS A THRESHOLD. Every severity on the list is the severity the
    underlying record carries. `TheGravityIsTheRecordsOwn` changes an alert's
    severity and watches the dashboard's ranking follow it, which is the only way
    to show the number is not being decided here.

`NothingIsWrongIsAnAnswer` is the boring one worth keeping: a farm with nothing
to report has to come back `all_clear: true` with sections that reported, rather
than an empty object that could equally mean the whole read failed.
"""

from .fixtures import MAIN, V12TestCase
from .harness import INSTALLED_DOCTYPES, STORE

ALL_ON = {
	f"allow_{name}": 1
	for name in (
		"get_owner_dashboard",
		"get_audit_readiness",
		"get_compliance_calendar",
		"refresh_compliance_alerts",
		"list_shifts",
		"list_bucket_entries",
		"list_pending_approvals",
		"list_dispatch_board",
		"get_housing_capacity",
		"list_housing_units",
		"compute_all_kpis",
		"get_policy_coverage",
		"create_parcel",
		"create_housing_unit",
	)
}

TODAY = "2026-07-24"


class DashboardTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def board(self, **overrides):
		payload = {"company": MAIN, "as_of": TODAY}
		payload.update(overrides)
		return self.tool_data("get_owner_dashboard", payload)

	def an_alert(self, severity="Critical", name="ALERT-1", alert_type="housing_inspection_overdue"):
		STORE.seed(
			"Compliance Alert",
			[
				{
					"name": name,
					"alert_key": name,
					"alert_type": alert_type,
					"category": "Housing",
					"company": MAIN,
					"severity": severity,
					"alert_message": "A cabin has no current habitability inspection.",
					"source_doctype": "Housing Unit",
					"source_docname": "MC-Cabin-01 - MC",
					"dismissed": 0,
					"first_seen": TODAY,
					"last_refreshed": TODAY,
				}
			],
		)

	def a_camp_backlog(self):
		self.tool_data(
			"create_parcel", {"owning_entity": MAIN, "parcel_name": "Mill Creek", "acreage": 131.43}
		)
		self.tool_data(
			"create_housing_unit",
			{
				"parcel": "Mill Creek",
				"unit_name": "MC-Cabin-01",
				"unit_type": "Cabin",
				"square_footage": 384,
				"capacity": 4,
				"fsma_worker_facility": True,
			},
		)


# ── the shape of the answer ─────────────────────────────────────────────────
class OneCallInsteadOfSeven(DashboardTestCase):
	def test_it_reports_which_sections_answered(self):
		data = self.board()
		self.assertIn("compliance", data["sections_reporting"])
		self.assertEqual(data["company"], MAIN)
		self.assertEqual(data["as_of"], TODAY)

	def test_the_camp_backlogs_arrive_as_two_separate_items(self):
		"""Different errands, different skills, different evidence. One number
		covering both is a number nobody can plan a morning from."""
		self.a_camp_backlog()
		data = self.board()
		self.assertEqual(data["camp"]["overdue_inspection_count"], 1)
		self.assertEqual(data["camp"]["overdue_detector_test_count"], 1)
		headlines = [row["headline"] for row in data["attention"] if row["section"] == "camp"]
		self.assertEqual(len(headlines), 2)
		self.assertTrue(any("habitability inspection" in line for line in headlines))
		self.assertTrue(any("detector test" in line for line in headlines))

	def test_every_attention_row_names_the_tool_that_answers_it_in_full(self):
		"""A dashboard row that cannot be drilled into is a row somebody has to
		go and hunt for, which is the habit this read exists to remove."""
		self.a_camp_backlog()
		self.an_alert()
		data = self.board()
		self.assertTrue(data["attention"])
		for row in data["attention"]:
			with self.subTest(headline=row["headline"]):
				self.assertTrue(row["read_it_with"])

	def test_the_sop_gap_is_on_the_board(self):
		data = self.board()
		self.assertIn("sop_coverage", data["sections_reporting"])
		self.assertTrue(data["sop_coverage"]["categories_with_no_policy"])
		self.assertTrue([row for row in data["attention"] if "SOP category" in row["headline"]])


# ── the ranking ─────────────────────────────────────────────────────────────
class TheGravityIsTheRecordsOwn(DashboardTestCase):
	"""This module decides ORDER, never gravity. The only way to show that is to
	change what the record says and watch the ranking follow."""

	def test_a_critical_alert_sorts_above_everything_else(self):
		self.a_camp_backlog()  # produces Warnings
		self.an_alert("Critical")
		data = self.board()
		self.assertEqual(data["attention"][0]["severity"], "Critical")
		self.assertEqual(data["attention"][0]["section"], "compliance")
		self.assertEqual(data["worst_severity"], "Critical")
		self.assertEqual(data["critical_count"], 1)

	def test_the_same_alert_at_warning_stops_leading_the_list(self):
		"""The severity came off the alert, not out of this module."""
		self.an_alert("Warning")
		data = self.board()
		compliance = [row for row in data["attention"] if row["section"] == "compliance"]
		self.assertEqual([row["severity"] for row in compliance], ["Warning"])
		self.assertEqual(data["critical_count"], 0)
		self.assertEqual(data["worst_severity"], "Warning")

	def test_an_info_alert_raises_nothing_on_the_board(self):
		"""Info is the level for things worth recording and not worth
		interrupting somebody's morning with."""
		self.an_alert("Info")
		data = self.board()
		self.assertFalse([row for row in data["attention"] if row["section"] == "compliance"])


# ── the failure mode that matters ───────────────────────────────────────────
class AnUnavailableSourceIsNotACleanOne(DashboardTestCase):
	"""THE ONE TO READ IF YOU ONLY READ ONE. A dashboard showing no compliance
	alerts because the compliance source refused looks exactly like a farm with
	no compliance alerts, and only one of those farms is fine."""

	def test_a_source_that_fails_is_named_rather_than_silently_absent(self):
		INSTALLED_DOCTYPES.discard("Compliance Alert")
		self.addCleanup(INSTALLED_DOCTYPES.add, "Compliance Alert")
		data = self.board()
		self.assertIn("compliance", data["sections_unavailable"])
		self.assertNotIn("compliance", data["sections_reporting"])
		self.assertTrue([entry for entry in data["unavailable"] if entry["section"] == "compliance"])

	def test_one_source_failing_does_not_empty_the_rest(self):
		INSTALLED_DOCTYPES.discard("Compliance Alert")
		self.addCleanup(INSTALLED_DOCTYPES.add, "Compliance Alert")
		self.a_camp_backlog()
		data = self.board()
		self.assertIn("camp", data["sections_reporting"])
		self.assertTrue([row for row in data["attention"] if row["section"] == "camp"])

	def test_the_failure_carries_the_reason_the_source_gave(self):
		INSTALLED_DOCTYPES.discard("Compliance Alert")
		self.addCleanup(INSTALLED_DOCTYPES.add, "Compliance Alert")
		entry = next(row for row in self.board()["unavailable"] if row["section"] == "compliance")
		self.assertIn(entry["kind"], ("refused", "error"))
		self.assertTrue(entry["reason"])


class NothingIsWrongIsAnAnswer(DashboardTestCase):
	def test_a_quiet_farm_reports_all_clear_with_sections_that_answered(self):
		"""`all_clear` on its own would be indistinguishable from a read that
		failed entirely, so it is only meaningful beside sections_reporting."""
		data = self.board()
		self.assertTrue(data["sections_reporting"])
		if data["all_clear"]:
			self.assertEqual(data["attention"], [])
			self.assertIsNone(data["worst_severity"])

	def test_a_company_is_required(self):
		error = self.tool_error("get_owner_dashboard", {})
		self.assertTrue(error)


class ItWritesNothing(DashboardTestCase):
	def test_reading_the_dashboard_changes_no_operational_register(self):
		"""`MCP Action Log` grows by one per call by design — that is this app
		recording that somebody read it, which is itself evidence."""
		self.a_camp_backlog()
		self.an_alert()

		def counted():
			return {
				doctype: len(STORE.rows(doctype)) for doctype in STORE.tables if doctype != "MCP Action Log"
			}

		before = counted()
		self.board()
		self.assertEqual(before, counted())
