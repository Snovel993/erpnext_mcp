# SPDX-License-Identifier: MIT
"""The three aggregate reports, and the black hole each of them closes.

WHAT THESE THREE HAVE IN COMMON is the reason they are one suite. Training
records, accident investigations and spray applications were all being WRITTEN
carefully and read back one document at a time, and none of the three had a call
that answered the question the data exists to answer: is the crew trained, what
goes on the 300 log this year, how much of that product went onto that block.
Data went in and no aggregate came out, so the aggregate was somebody's
spreadsheet — which is the shape of a number nobody can reproduce twelve months
later in front of the person asking for it.

SEVEN CLAIMS.

1. `MissingIsAStatus` — the training matrix reports the person with NO record of
   a curriculum, which is the one thing `list_trainings` structurally cannot do:
   there, an absence is no row, which is to say nowhere.

2. `TheMatrixIsAsOfADate` — `as_of_date` reaches both the record selection and
   the expiry arithmetic, so a report run for last year's audit does not know
   about training completed since.

3. `TheLogIsNotTheRegister` — only cases determined recordable reach the 300 log,
   the undetermined ones are NAMED rather than dropped, and every case is
   counted once at its most severe outcome.

4. `TheRatesWillNotBeInvented` — with no hours worked, TRIR/DART/LTIR come back
   None and not 0.0, because a zero rate reads as a perfect safety year.

5. `TheDenominatorCanBeSupplied` — and when it is, the three formulas are
   `cases × 200,000 / hours`.

6. `ProductTotalsAreRateTimesBlockAcres` — never the tank total spread evenly
   across blocks of unequal size, and never summed across two units.

7. `TheGuards` — the role gates, the company scope, and the fact that all four
   are reads that write nothing.
"""

import frappe

from erpnext_mcp import compliance_fields, roles

from .fixtures import (
	MAIN,
	OTHER,
	SPRAY,
	STORES,
	V12TestCase,
	install_hrms,
	seed_masters,
	seed_stock,
)
from .harness import ROLES, STORE, set_roles

ON = {
	f"allow_{name}": 1
	for name in (
		"record_training",
		"list_trainings",
		"get_training_compliance_report",
		"create_accident_report",
		"update_accident_investigation",
		"list_accident_reports",
		"get_osha_300_log",
		"get_osha_300a_summary",
		"create_spray_application",
		"list_spray_applications",
		"get_spray_application_report",
		"create_parcel",
		"create_field",
	)
}

TRAINEE = "HR-EMP-00002"  # Ben Packhouse, Active, at MAIN
SUPERVISOR = "HR-EMP-00001"  # Ada Orchard, Active, at MAIN

TOPICS = "Heat index, water, shade, symptoms, reporting, emergency response"

WPS = "WPS Handler Training"
HEAT = "Heat Illness Prevention"

BLOCK = "Yellow Camp Block 3 - MC"
BLOCK_TWO = "Yellow Camp Block 4 - MC"

NUTRIENT = "FOLIAR-N"


def days_out(count: int) -> str:
	return str(frappe.utils.add_days(frappe.utils.today(), count))


def hours_ago(count: int) -> str:
	return str(frappe.utils.add_to_date(frappe.utils.now(), hours=-count))


class ReportTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ON)
		install_hrms()
		self._roles_before = {user: list(held) for user, held in ROLES.items()}
		self.addCleanup(self._restore_roles)
		roles.install_roles()

	def _restore_roles(self):
		ROLES.clear()
		ROLES.update(self._roles_before)

	def a_training(self, **overrides):
		payload = {
			"employee": TRAINEE,
			"training_type": HEAT,
			"completed_date": frappe.utils.today(),
			"regimes": ["OR-OSHA"],
			"content_topics_covered": TOPICS,
		}
		payload.update(overrides)
		return self.tool_data("record_training", payload)

	def an_employee_at(self, company: str, name: str, docname: str, designation: str = "") -> str:
		row = {
			"name": docname,
			"employee_name": name,
			"status": "Active",
			"date_of_joining": "2025-01-01",
			"company": company,
		}
		if designation:
			row["designation"] = designation
		STORE.seed("Employee", [row])
		return docname

	def matrix(self, **kw):
		payload = {"company": MAIN}
		payload.update(kw)
		return self.tool_data("get_training_compliance_report", payload)

	def cell(self, data: dict, employee: str, curriculum: str) -> dict:
		row = next(entry for entry in data["matrix"] if entry["employee"] == employee)
		return row["requirements"][curriculum]


# ── 1 ───────────────────────────────────────────────────────────────────────
class MissingIsAStatus(ReportTestCase):
	"""The whole reason the matrix is not a filter over the register."""

	def test_a_person_with_no_record_of_a_curriculum_has_a_cell_saying_so(self):
		self.a_training(training_type=WPS, regimes=["WPS"], expires_date=days_out(300))
		other = self.an_employee_at(MAIN, "Nula Newhire", "HR-EMP-NEW")

		data = self.matrix()

		self.assertEqual(self.cell(data, TRAINEE, WPS)["status"], "current")
		# The person the register has nothing about. In `list_trainings` this is
		# no row at all; here it is a cell with a name on it.
		missing = self.cell(data, other, WPS)
		self.assertEqual(missing["status"], "missing")
		self.assertIsNone(missing["record"])
		self.assertIsNone(missing["completed_date"])

	def test_the_register_genuinely_cannot_report_it(self):
		"""Not a strawman: the same fact, asked of `list_trainings`, is absent."""
		self.a_training(training_type=WPS, regimes=["WPS"], expires_date=days_out(300))
		self.an_employee_at(MAIN, "Nula Newhire", "HR-EMP-NEW")

		register = self.tool_data("list_trainings", {"company": MAIN})
		named = {row["employee"] for row in register["records"]}

		self.assertNotIn("HR-EMP-NEW", named)
		self.assertIn("HR-EMP-NEW", {row["employee"] for row in self.matrix()["matrix"]})

	def test_the_four_statuses_are_each_reachable(self):
		self.a_training(training_type=WPS, regimes=["WPS"], expires_date=days_out(300))
		self.a_training(training_type=HEAT, regimes=["OR-OSHA"], expires_date=days_out(30))
		lapsed = self.an_employee_at(MAIN, "Otto Lapsed", "HR-EMP-LAPSED")
		self.a_training(
			employee=lapsed,
			training_type=WPS,
			regimes=["WPS"],
			completed_date=days_out(-400),
			expires_date=days_out(-35),
		)

		data = self.matrix()

		self.assertEqual(self.cell(data, TRAINEE, WPS)["status"], "current")
		self.assertEqual(self.cell(data, TRAINEE, HEAT)["status"], "due_soon")
		self.assertEqual(self.cell(data, lapsed, WPS)["status"], "expired")
		self.assertEqual(self.cell(data, lapsed, HEAT)["status"], "missing")

	def test_the_summary_splits_the_roster_three_ways(self):
		self.a_training(training_type=WPS, regimes=["WPS"], expires_date=days_out(300))
		self.a_training(training_type=HEAT, regimes=["OR-OSHA"], expires_date=days_out(300))
		partial = self.an_employee_at(MAIN, "Pia Partial", "HR-EMP-PART")
		self.a_training(employee=partial, training_type=WPS, regimes=["WPS"], expires_date=days_out(300))
		self.an_employee_at(MAIN, "Nula Newhire", "HR-EMP-NEW")

		summary = self.matrix()["summary"]

		self.assertEqual(summary["fully_compliant"], 1)
		self.assertEqual(summary["partially_compliant"], 1)
		# Ada, Cal and Nula hold none of the two curricula.
		self.assertGreaterEqual(summary["non_compliant"], 2)
		self.assertEqual(
			summary["total_employees"],
			summary["fully_compliant"]
			+ summary["partially_compliant"]
			+ summary["non_compliant"]
			+ summary["without_requirements"],
		)

	def test_it_says_the_requirement_axis_is_the_curriculum_master(self):
		"""The over-report is stated rather than presented as a finding: this site
		has no per-role requirement table, so a bookkeeper is held against WPS."""
		self.a_training(training_type=WPS, regimes=["WPS"], expires_date=days_out(300))
		data = self.matrix()
		self.assertIn("NO PER-ROLE REQUIREMENT TABLE", data["requirement_basis"])

	def test_a_regime_filter_narrows_the_columns_by_tag_not_by_substring(self):
		self.a_training(training_type=WPS, regimes=["WPS"], expires_date=days_out(300))
		self.a_training(training_type=HEAT, regimes=["OR-OSHA"], expires_date=days_out(300))

		data = self.matrix(regime="WPS")

		self.assertEqual([entry["training_type"] for entry in data["requirements"]], [WPS])
		self.assertEqual(data["regime"], "WPS")

	def test_an_unknown_regime_is_refused_with_the_vocabulary(self):
		message = self.tool_error("get_training_compliance_report", {"company": MAIN, "regime": "OSHA-ish"})
		self.assertIn("not one this app knows", message)

	def test_a_curriculum_nobody_has_run_is_refused_rather_than_reported_as_a_gap(self):
		"""Holding the crew against a course this operation has never run would
		report every one of them non-compliant on nothing."""
		message = self.tool_error(
			"get_training_compliance_report", {"company": MAIN, "training_type": "Forklift Rodeo"}
		)
		self.assertIn("Forklift Rodeo", message)
		self.assertIn("never run", message)


# ── 2 ───────────────────────────────────────────────────────────────────────
class TheMatrixIsAsOfADate(ReportTestCase):
	def test_training_completed_after_the_date_is_not_counted(self):
		"""A report run for last year's audit must not know about March."""
		self.a_training(training_type=WPS, regimes=["WPS"], completed_date=days_out(-5), expires_date=days_out(300))

		before = self.matrix(as_of_date=days_out(-10))
		after = self.matrix(as_of_date=frappe.utils.today())

		self.assertEqual(self.cell(before, TRAINEE, WPS)["status"], "missing")
		self.assertEqual(self.cell(after, TRAINEE, WPS)["status"], "current")

	def test_expiry_is_computed_against_the_same_date_the_records_were_selected_on(self):
		"""The two halves cannot disagree, because they are given one date."""
		self.a_training(
			training_type=WPS,
			regimes=["WPS"],
			completed_date=days_out(-200),
			expires_date=days_out(-30),
		)

		today = self.matrix()
		back_then = self.matrix(as_of_date=days_out(-150))

		self.assertEqual(self.cell(today, TRAINEE, WPS)["status"], "expired")
		self.assertEqual(self.cell(back_then, TRAINEE, WPS)["status"], "current")

	def test_the_latest_record_governs_and_the_earlier_one_is_not_deleted(self):
		self.a_training(
			training_type=WPS,
			regimes=["WPS"],
			completed_date=days_out(-400),
			expires_date=days_out(-35),
		)
		renewal = self.a_training(
			training_type=WPS,
			regimes=["WPS"],
			completed_date=days_out(-10),
			expires_date=days_out(355),
		)

		cell = self.cell(self.matrix(), TRAINEE, WPS)

		self.assertEqual(cell["status"], "current")
		self.assertEqual(cell["record"], renewal["name"])
		self.assertEqual(len(self.tool_data("list_trainings", {"employee": TRAINEE})["records"]), 2)

	def test_one_time_training_never_lapses(self):
		self.a_training(training_type=WPS, regimes=["WPS"], completed_date=days_out(-2000))
		cell = self.cell(self.matrix(), TRAINEE, WPS)
		self.assertEqual(cell["status"], "current")
		self.assertTrue(cell["one_time"])


class AccidentReportTestCase(ReportTestCase):
	"""Helpers only. The three claims below inherit these and not each other's
	assertions — a claim class that subclassed another would re-run every one of
	its tests under a second name, which reads as coverage and is arithmetic."""

	def a_report(self, **overrides):
		payload = {
			"occurred_at": hours_ago(4),
			"incident_description": "Caught a hand between the sorter belt and the guard rail.",
			"severity": "Medical Treatment",
			"injured_person": TRAINEE,
			"medical_treatment": "Medical Treatment Beyond First Aid",
			"immediate_actions": "Line locked out, first aid given, driven to the clinic.",
			"company": MAIN,
			"location_description": "Packing line 2",
		}
		payload.update(overrides)
		return self.tool_data("create_accident_report", payload)

	def determine(self, name: str, recordable: str = "Yes", **overrides):
		payload = {
			"report": name,
			"osha_recordable": recordable,
			"osha_determination_basis": "Sutures placed at the clinic — treatment beyond first aid.",
		}
		payload.update(overrides)
		return self.tool_data("update_accident_investigation", payload)

	def log(self, **kw):
		payload = {"company": MAIN, "year": int(frappe.utils.today()[:4])}
		payload.update(kw)
		return self.tool_data("get_osha_300_log", payload)

	def summary(self, **kw):
		payload = {"company": MAIN, "year": int(frappe.utils.today()[:4])}
		payload.update(kw)
		return self.tool_data("get_osha_300a_summary", payload)


# ── 3 ───────────────────────────────────────────────────────────────────────
class TheLogIsNotTheRegister(AccidentReportTestCase):
	def test_only_a_determined_recordable_case_reaches_the_log(self):
		recordable = self.a_report()
		self.determine(recordable["name"])
		self.a_report(severity="Near Miss", medical_treatment="None")

		data = self.log()

		self.assertEqual(data["case_count"], 1)
		self.assertEqual(data["cases"][0]["report"], recordable["name"])

	def test_an_undetermined_report_is_named_rather_than_dropped(self):
		"""A log that omitted them silently would present a partial year as a
		finished one — which is the shape of a document somebody signs."""
		pending = self.a_report()

		data = self.log()

		self.assertEqual(data["case_count"], 0)
		self.assertEqual(data["undetermined_count"], 1)
		self.assertEqual([row["report"] for row in data["undetermined_cases"]], [pending["name"]])
		self.assertTrue(any("Undetermined" in note for note in data["notes"]))

	def test_a_case_determined_not_recordable_is_on_neither_list(self):
		report = self.a_report()
		self.determine(
			report["name"],
			recordable="No",
			osha_determination_basis="Cleaned and a plaster applied. First aid under 1904.7(b)(5)(ii).",
		)

		data = self.log()

		self.assertEqual(data["case_count"], 0)
		self.assertEqual(data["undetermined_count"], 0)

	def test_cases_are_numbered_from_one_in_the_order_they_happened(self):
		first = self.a_report(occurred_at=hours_ago(48))
		second = self.a_report(occurred_at=hours_ago(4))
		self.determine(second["name"])
		self.determine(first["name"])

		cases = self.log()["cases"]

		self.assertEqual([row["case_number"] for row in cases], [1, 2])
		self.assertEqual(cases[0]["report"], first["name"])

	def test_every_case_is_classified_once_at_its_most_severe_outcome(self):
		away = self.a_report()["name"]
		self.determine(away, days_away_from_work=3, days_restricted_duty=5)
		restricted = self.a_report()["name"]
		self.determine(restricted, days_restricted_duty=4)
		other = self.a_report()["name"]
		self.determine(other)
		fatal = self.a_report(severity="Fatality")["name"]
		self.determine(fatal, days_away_from_work=9)

		by_case = {row["report"]: row["classify"] for row in self.log()["cases"]}

		# The days-away case ALSO has restricted days and is counted once.
		self.assertEqual(by_case[away], "days_away")
		self.assertEqual(by_case[restricted], "restricted")
		self.assertEqual(by_case[other], "other_recordable")
		self.assertEqual(by_case[fatal], "death")

	def test_the_300a_columns_add_up_to_the_case_count(self):
		"""Which they only do because the classification is exclusive."""
		for days_away, days_restricted, severity in (
			(3, 5, "Medical Treatment"),
			(0, 4, "Medical Treatment"),
			(0, 0, "Medical Treatment"),
			(9, 0, "Fatality"),
		):
			report = self.a_report(severity=severity)
			self.determine(
				report["name"], days_away_from_work=days_away, days_restricted_duty=days_restricted
			)

		data = self.summary()

		self.assertEqual(data["total_cases"], 4)
		self.assertEqual(
			data["total_deaths"]
			+ data["total_days_away_cases"]
			+ data["total_restricted_cases"]
			+ data["total_other_recordable"],
			data["total_cases"],
		)

	def test_the_log_carries_the_columns_the_form_asks_for(self):
		report = self.a_report(
			injury_type="Crush",
			body_part="Left hand",
			location_description="Packing line 2",
		)
		self.determine(report["name"], days_away_from_work=2, days_restricted_duty=6)

		row = self.log()["cases"][0]

		self.assertEqual(row["employee_name"], "Ben Packhouse")
		self.assertEqual(row["job_title"], "Operator")
		self.assertEqual(row["date_of_injury"], frappe.utils.today())
		self.assertEqual(row["where_event_occurred"], "Packing line 2")
		self.assertIn("sorter belt", row["description"])
		self.assertEqual(row["days_away_from_work"], 2)
		self.assertEqual(row["days_on_restriction"], 6)

	def test_day_counts_are_capped_at_180_for_the_totals_and_raw_beside_them(self):
		report = self.a_report()
		self.determine(report["name"], days_away_from_work=240)

		row = self.log()["cases"][0]
		data = self.summary()

		self.assertEqual(row["days_away_from_work"], 240)
		self.assertEqual(row["days_away_counted"], 180)
		self.assertEqual(data["total_days_away"], 180)
		self.assertTrue(any("180" in note for note in self.log()["notes"]))

	def test_a_year_is_a_calendar_year_and_a_case_outside_it_is_not_on_the_log(self):
		report = self.a_report()
		self.determine(report["name"])
		this_year = int(frappe.utils.today()[:4])

		self.assertEqual(self.log(year=this_year)["case_count"], 1)
		self.assertEqual(self.log(year=this_year - 1)["case_count"], 0)

	def test_a_missing_year_is_refused_because_1904_keeps_the_log_by_calendar_year(self):
		message = self.tool_error("get_osha_300_log", {"company": MAIN})
		self.assertIn("CALENDAR year", message)

	def test_the_privacy_rule_is_named_because_the_app_cannot_apply_it(self):
		report = self.a_report()
		self.determine(report["name"])
		self.assertTrue(any("1904.29(b)(7)" in note for note in self.log()["notes"]))

	def test_a_case_from_another_company_is_not_on_this_log(self):
		STORE.seed("Employee", [{"name": "HR-EMP-OTHER", "employee_name": "Otto Elsewhere", "company": OTHER, "status": "Active"}])
		mine = self.a_report()
		self.determine(mine["name"])
		theirs = self.a_report(company=OTHER, injured_person="HR-EMP-OTHER")
		self.determine(theirs["name"])

		self.assertEqual([row["report"] for row in self.log()["cases"]], [mine["name"]])


# ── 4 ───────────────────────────────────────────────────────────────────────
class TheRatesWillNotBeInvented(AccidentReportTestCase):
	def test_with_no_hours_worked_the_rates_are_none_and_not_zero(self):
		"""A rate of 0.0 reads on every screen as a perfect safety year, and it
		is the answer an operation with no recorded hours would get."""
		report = self.a_report()
		self.determine(report["name"], days_away_from_work=3)

		data = self.summary()

		self.assertIsNone(data["total_hours_worked"])
		self.assertIsNone(data["rates"]["TRIR"])
		self.assertIsNone(data["rates"]["DART"])
		self.assertIsNone(data["rates"]["LTIR"])
		self.assertTrue(any("perfect safety year" in note for note in data["notes"]))

	def test_a_year_with_no_cases_at_all_still_reports_no_rate_rather_than_zero(self):
		data = self.summary()
		self.assertEqual(data["total_cases"], 0)
		self.assertIsNone(data["rates"]["TRIR"])


# ── 5 ───────────────────────────────────────────────────────────────────────
class TheDenominatorCanBeSupplied(AccidentReportTestCase):
	def test_the_three_formulas_are_cases_times_200000_over_hours(self):
		away = self.a_report()
		self.determine(away["name"], days_away_from_work=3)
		restricted = self.a_report()
		self.determine(restricted["name"], days_restricted_duty=4)
		other = self.a_report()
		self.determine(other["name"])

		data = self.summary(total_hours_worked=100_000)

		self.assertEqual(data["total_hours_worked"], 100_000)
		self.assertEqual(data["total_hours_worked_source"], "supplied by the caller")
		# Three recordable cases over 100,000 hours.
		self.assertEqual(data["rates"]["TRIR"], round(3 * 200_000 / 100_000, 2))
		# DART is days-away plus restricted; the third case is neither.
		self.assertEqual(data["rates"]["DART"], round(2 * 200_000 / 100_000, 2))
		self.assertEqual(data["rates"]["LTIR"], round(1 * 200_000 / 100_000, 2))

	def test_a_fatality_is_in_trir_and_not_in_dart(self):
		fatal = self.a_report(severity="Fatality")
		self.determine(fatal["name"])

		data = self.summary(total_hours_worked=200_000)

		self.assertEqual(data["rates"]["TRIR"], 1.0)
		self.assertEqual(data["rates"]["DART"], 0.0)

	def test_average_employees_is_taken_from_the_caller_when_given(self):
		data = self.summary(total_hours_worked=200_000, average_employees=42)
		self.assertEqual(data["average_employees"], 42)
		self.assertEqual(data["average_employees_source"], "supplied by the caller")

	def test_a_non_numeric_denominator_is_refused_rather_than_ignored(self):
		message = self.tool_error(
			"get_osha_300a_summary",
			{"company": MAIN, "year": int(frappe.utils.today()[:4]), "total_hours_worked": "lots"},
		)
		self.assertIn("total_hours_worked", message)

	def test_the_undetermined_cases_are_declared_as_a_floor_on_every_rate(self):
		self.a_report()
		data = self.summary(total_hours_worked=200_000)
		self.assertEqual(data["undetermined_count"], 1)
		self.assertTrue(any("floor" in note for note in data["notes"]))


# ── 6 ───────────────────────────────────────────────────────────────────────
class ProductTotalsAreRateTimesBlockAcres(ReportTestCase):
	def setUp(self):
		super().setUp()
		seed_masters()
		seed_stock()
		compliance_fields.install_compliance_fields()
		STORE.seed("UOM", [{"name": "Gal", "enabled": 1}, {"name": "Lb", "enabled": 1}])
		STORE.seed(
			"Item",
			[
				{
					"name": NUTRIENT,
					"item_code": NUTRIENT,
					"item_name": "Foliar Nitrogen",
					"stock_uom": "Gal",
					"is_stock_item": 1,
					"disabled": 0,
					"item_defaults": [{"company": MAIN, "default_warehouse": STORES}],
					"reorder_levels": [],
					"rei_hours": 0,
					"phi_days": 0,
				}
			],
		)
		spray = STORE.get_raw("Item", SPRAY)
		spray["rei_hours"] = 4
		spray["phi_days"] = 14
		self._farm()

	def _farm(self):
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
		for name in ("Yellow Camp Block 3", "Yellow Camp Block 4"):
			self.tool_data(
				"create_field",
				{
					"parcel": "Mill Creek",
					"field_name": name,
					"acreage": 12.5,
					"variety": "Bing",
					"planting_year": 1998,
					"condition": "Good",
				},
			)

	def an_application(self, blocks=(BLOCK,), **kw):
		payload = {"blocks": list(blocks), "company": MAIN}
		payload.setdefault("products", [{"item_code": SPRAY, "rate_per_acre": 5, "rate_uom": "Lb"}])
		payload.update(kw)
		return self.tool_data("create_spray_application", payload)

	def report(self, **kw):
		payload = {"company": MAIN}
		payload.update(kw)
		return self.tool_data("get_spray_application_report", payload)

	def product(self, data: dict, item: str) -> dict:
		return next(entry for entry in data["products"] if entry["product"] == item)

	def test_each_block_gets_rate_times_its_own_acres_not_the_tank_split_evenly(self):
		self.an_application(
			blocks=[{"block": BLOCK, "acres": 10}, {"block": BLOCK_TWO, "acres": 30}],
			products=[{"item_code": SPRAY, "rate_per_acre": 5, "rate_uom": "Lb"}],
		)

		entry = self.product(self.report(), SPRAY)

		self.assertEqual(entry["blocks"][BLOCK]["quantity_by_uom"]["Lb"], 50.0)
		self.assertEqual(entry["blocks"][BLOCK_TWO]["quantity_by_uom"]["Lb"], 150.0)
		# An even split would have given 100 apiece, which was true of neither.
		self.assertEqual(entry["total_by_uom"]["Lb"], 200.0)
		self.assertEqual(entry["total_acres_treated"], 40.0)

	def test_two_units_of_one_product_are_two_totals_and_are_named(self):
		self.an_application(products=[{"item_code": SPRAY, "rate_per_acre": 5, "rate_uom": "Lb"}])
		self.an_application(products=[{"item_code": SPRAY, "rate_per_acre": 2, "rate_uom": "Gal"}])

		data = self.report()
		entry = self.product(data, SPRAY)

		self.assertEqual(sorted(entry["total_by_uom"]), ["Gal", "Lb"])
		self.assertIn(SPRAY, data["mixed_unit_products"])
		self.assertTrue(any("density" in note for note in data["notes"]))

	def test_a_planned_or_cancelled_application_is_excluded_and_counted(self):
		self.an_application()
		self.an_application(status="Planned")

		data = self.report()

		self.assertEqual(data["application_count"], 1)
		self.assertEqual(data["excluded"]["planned"], 1)
		self.assertTrue(any("not an application" in note for note in data["notes"]))

	def test_it_groups_by_product_and_then_by_block(self):
		self.an_application(
			blocks=[{"block": BLOCK, "acres": 10}],
			products=[
				{"item_code": SPRAY, "rate_per_acre": 5, "rate_uom": "Lb"},
				{"item_code": NUTRIENT, "rate_per_acre": 2, "rate_uom": "Gal"},
			],
		)
		self.an_application(
			blocks=[{"block": BLOCK_TWO, "acres": 20}],
			products=[{"item_code": SPRAY, "rate_per_acre": 5, "rate_uom": "Lb"}],
		)

		data = self.report()

		self.assertEqual(data["product_count"], 2)
		self.assertEqual(sorted(self.product(data, SPRAY)["blocks"]), sorted([BLOCK, BLOCK_TWO]))
		self.assertEqual(list(self.product(data, NUTRIENT)["blocks"]), [BLOCK])
		self.assertEqual(sorted(data["by_block"][BLOCK]["products"]), sorted([SPRAY, NUTRIENT]))

	def test_it_carries_the_dates_applicators_and_rei_a_records_inspection_asks_for(self):
		self.an_application(blocks=[{"block": BLOCK, "acres": 10}], applicator="Administrator")

		entry = self.product(self.report(), SPRAY)

		self.assertEqual(entry["rei_hours"], 4.0)
		self.assertEqual(entry["applicators"], ["Administrator"])
		self.assertEqual(entry["blocks"][BLOCK]["dates"], [frappe.utils.today()])
		self.assertEqual(entry["first_application"], frappe.utils.today())

	def test_a_product_filter_reports_that_product_alone(self):
		self.an_application(
			products=[
				{"item_code": SPRAY, "rate_per_acre": 5, "rate_uom": "Lb"},
				{"item_code": NUTRIENT, "rate_per_acre": 2, "rate_uom": "Gal"},
			]
		)

		data = self.report(product=NUTRIENT)

		self.assertEqual([entry["product"] for entry in data["products"]], [NUTRIENT])

	def test_a_block_filter_reports_that_block_alone(self):
		self.an_application(blocks=[{"block": BLOCK, "acres": 10}])
		self.an_application(blocks=[{"block": BLOCK_TWO, "acres": 20}])

		data = self.report(block=BLOCK_TWO)

		self.assertEqual(list(data["by_block"]), [BLOCK_TWO])
		self.assertEqual(self.product(data, SPRAY)["total_by_uom"]["Lb"], 100.0)

	def test_the_window_defaults_to_the_calendar_year_and_says_so(self):
		self.an_application()
		data = self.report()
		self.assertTrue(data["window_defaulted"])
		self.assertEqual(data["date_from"], f"{frappe.utils.today()[:4]}-01-01")
		self.assertTrue(any("calendar year to date" in note for note in data["notes"]))

	def test_a_backwards_window_is_refused(self):
		message = self.tool_error(
			"get_spray_application_report",
			{"company": MAIN, "date_from": days_out(10), "date_to": days_out(-10)},
		)
		self.assertIn("before", message)

	def test_an_empty_season_says_so_rather_than_reading_as_no_spraying(self):
		data = self.report(date_from=days_out(-400), date_to=days_out(-300))
		self.assertEqual(data["product_count"], 0)
		self.assertTrue(any("no spraying" in note for note in data["notes"]))


# ── 7 ───────────────────────────────────────────────────────────────────────
class TheGuards(ReportTestCase):
	def test_the_training_matrix_is_an_hr_read(self):
		"""It names by name who has had nothing, which is a personnel document."""
		set_roles("Administrator", ["Accounts User"])
		message = self.tool_error("get_training_compliance_report", {"company": MAIN})
		self.assertIn("may not change the personnel register", message)

	def test_a_farm_manager_may_read_the_matrix(self):
		set_roles("Administrator", ["Farm Manager"])
		self.assertEqual(self.matrix()["company"], MAIN)

	def test_a_scoped_principal_cannot_read_another_entitys_matrix(self):
		STORE.seed(
			"User Permission",
			[
				{
					"name": "UP-SCOPE-MAIN",
					"user": "Administrator",
					"allow": "Company",
					"for_value": MAIN,
					"apply_to_all_doctypes": 1,
					"is_default": 1,
				}
			],
		)
		message = self.tool_error("get_training_compliance_report", {"company": OTHER})
		self.assertIn("has no access to company", message)

	def test_all_four_are_reads_and_are_on_by_default(self):
		from erpnext_mcp import registry

		for name in (
			"get_training_compliance_report",
			"get_osha_300_log",
			"get_osha_300a_summary",
			"get_spray_application_report",
		):
			with self.subTest(tool=name):
				self.assertIn(name, registry.READ_TOOLS)
				self.assertFalse(registry.TOOLS[name]["mutating"])

	def test_a_disabled_switch_hides_the_report(self):
		self.configure(enabled=1, allow_get_osha_300_log=0)
		message = self.tool_error(
			"get_osha_300_log", {"company": MAIN, "year": int(frappe.utils.today()[:4])}
		)
		self.assertIn("get_osha_300_log", message)

	def test_the_handset_cannot_choose_the_denominator_of_a_rate(self):
		"""`get_osha_300a_summary` takes `total_hours_worked` and
		`average_employees` so a desk can supply a figure from payroll. The
		mobile wrapper declares neither, so `routes.bind` drops both — and a
		handset that could set the denominator of a TRIR could set the TRIR,
		which is a number that goes on a posted form."""
		from erpnext_mcp.farmops_api import routes

		accepted = routes.accepted_arguments(
			routes.BY_PATH["/mobile/get_osha_300a_summary"].handler
		)

		self.assertEqual(accepted, {"company", "year"})
		self.assertNotIn("total_hours_worked", accepted)
		self.assertNotIn("average_employees", accepted)

	def test_all_four_reports_are_reachable_from_a_handset(self):
		from erpnext_mcp.farmops_api import routes

		for name in (
			"get_training_compliance_report",
			"get_osha_300_log",
			"get_osha_300a_summary",
			"get_spray_application_report",
		):
			with self.subTest(method=name):
				route = routes.BY_PATH[f"/mobile/{name}"]
				self.assertEqual(route.handler.farm_ops_method, name)
				self.assertFalse(route.mutating)

	def test_none_of_them_writes_anything(self):
		self.a_training(training_type=WPS, regimes=["WPS"], expires_date=days_out(300))
		before = {doctype: len(rows) for doctype, rows in STORE.tables.items()}

		self.matrix()
		self.tool_data("get_osha_300_log", {"company": MAIN, "year": int(frappe.utils.today()[:4])})
		self.tool_data("get_osha_300a_summary", {"company": MAIN, "year": int(frappe.utils.today()[:4])})

		after = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		for doctype, count in before.items():
			# The audit log is the one register a read is supposed to grow.
			if doctype in ("MCP Action Log", "Audit Event"):
				continue
			with self.subTest(doctype=doctype):
				self.assertEqual(after.get(doctype, 0), count)
