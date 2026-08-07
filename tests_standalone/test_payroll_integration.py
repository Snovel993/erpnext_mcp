# SPDX-License-Identifier: MIT
"""Payroll off the shift register — v0.35.0.

THE CLAIM BEHIND THE RELEASE is that a payroll run should read the hours the
foreman already recorded rather than ask somebody to retype them. v0.30.0 built
an engine that could compute a slip and v0.19.3 built a register that recorded
the shifts, and the join between them returned the crew's span for every worker
with zero overtime and zero piece units. Everything here is about that join.

EIGHT CLAIMS.

1. `TheSegmentIsTheUnit` — hours are aggregated from each worker's OWN
   joined_at/left_at, not the crew's span, across several employees, several
   shifts and two states.

2. `OvertimeIsWeekly` — the 40-hour threshold is tested per workweek and not per
   pay period, walked chronologically, and the state the overtime landed in is
   the state the worker was actually in when they crossed forty.

3. `BreaksAreTwoKinds` — a paid rest break stays inside total_hours and is paid
   at the average piece rate; an unpaid meal period comes off the span and
   counts toward neither hours nor the overtime threshold.

4. `PieceUnitsReachThePayroll` — units on a crew row, in a per-employee map and
   in an attached piece-row list all arrive at the same place, and a piece-rate
   structure turns them into gross.

5. `TheCrossStateWorker` — OR shifts and WA shifts in one period produce hours,
   wages and taxes in both, allocated by the hours actually worked in each.

6. `MinimumWageIsPerState` — checked against each state's own floor using the
   real shift hours, reported with the shortfall priced, and NOT silently
   topped up.

7. `EndToEnd` — shifts seeded on the site, read through the tools, computed and
   written as a Farm Payroll Entry whose slips match what the preview said.

8. `EdgeCases` — no shifts, a partial week, an employee with no salary
   structure, an unclosed shift, and a period whose dates are backwards.

Plus `TheTools`, which is the switch posture: two reads on by default, the run
off by default, and every one of them refused by name when its switch is off.
"""

from erpnext_mcp import payroll_integration as pi
from erpnext_mcp.payroll_calc import calculate_full_payroll
from erpnext_mcp.withholding import ANNUAL_BRACKETS, PERIODS_PER_YEAR

from .fixtures import MAIN, OTHER, V12TestCase, install_hrms
from .harness import STORE, add_field, register_doctype

WORKER = "HR-EMP-00001"
PICKER = "HR-EMP-00002"
DRIVER = "HR-EMP-00003"

#: The register's names, so a crew row carries what a real one carries.
NAMES = {WORKER: "Ana Reyes", PICKER: "Beto Cruz", DRIVER: "Carla Mota"}

#: Monday. Every period in this file starts on one, so a workweek index and a
#: calendar week are the same thing and an assertion about "week two" can be
#: read against a diary.
PERIOD_START = "2025-06-02"
PERIOD_END = "2025-06-15"

ON = {
	f"allow_{name}": 1
	for name in (
		"get_employee_timesheet_summary",
		"preview_payroll_for_period",
		"run_payroll_for_period",
		"get_salary_structure",
		"list_salary_structures",
		"preview_payroll",
		"get_payroll_entry",
		"list_payroll_entries",
		"create_salary_structure",
		"deactivate_salary_structure",
		"calculate_payroll",
		"submit_payroll",
		"submit_w4",
	)
}


def shift(
	name,
	day,
	start="06:00:00",
	end="15:00:00",
	state="OR",
	crew=(),
	company=MAIN,
	**extra,
):
	"""A Farm Shift row with a crew, in the shape the site stores it."""
	row = {
		"name": name,
		"company": company,
		"shift_type": "Harvest",
		"status": "Closed",
		"cancelled": 0,
		"work_state": state,
		"start_datetime": f"{day} {start}",
		"end_datetime": f"{day} {end}" if end else None,
		"crew": [dict(member) for member in crew],
	}
	row.update(extra)
	return row


def member(employee, joined=None, left=None, name=None, **extra):
	"""One crew row. `joined`/`left` are full datetimes or None for the span."""
	row = {
		"employee": employee,
		"employee_name": name or NAMES.get(employee, employee),
		"joined_at": joined,
		"left_at": left,
	}
	row.update(extra)
	return row


def bucket(name, employee, day, units=None, at="10:00:00", company=MAIN):
	"""One Bucket Log Entry row, as the bridge writes it."""
	row = {
		"name": name,
		"company": company,
		"picker_id": employee,
		"logged_at": f"{day} {at}",
	}
	if units is not None:
		row["bucket_count"] = units
	return row


class IntegrationTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ON)
		install_hrms()
		self._seed_fica()
		self._seed_federal_brackets()
		self._seed_employees()
		self._seed_state_configs()

	# ── fixtures ──────────────────────────────────────────────────────

	def _seed_fica(self):
		STORE.singles["FICA Configuration"] = {
			"doctype": "FICA Configuration",
			"tax_year": "2025",
			"social_security_rate_employee": "6.2",
			"social_security_rate_employer": "6.2",
			"social_security_wage_base": "176100",
			"medicare_rate_employee": "1.45",
			"medicare_rate_employer": "1.45",
			"additional_medicare_threshold": "200000",
			"additional_medicare_rate": "0.9",
			"futa_rate": "6.0",
			"futa_wage_base": "7000",
			"futa_state_credit_max": "5.4",
		}

	def _seed_federal_brackets(self):
		brackets = []
		for filing_status, annual in ANNUAL_BRACKETS.items():
			for period_name, periods in PERIODS_PER_YEAR.items():
				for bracket in annual:
					floor = bracket["bracket_floor"] / periods
					ceiling = bracket["bracket_ceiling"] / periods if bracket["bracket_ceiling"] else None
					brackets.append(
						{
							"name": f"FTB-{filing_status[:3]}-{period_name[:3]}-{floor:.0f}",
							"tax_year": 2025,
							"filing_status": filing_status,
							"payroll_period": period_name,
							"bracket_floor": round(floor, 2),
							"bracket_ceiling": round(ceiling, 2) if ceiling else None,
							"base_tax": round(bracket["base_tax"] / periods, 2),
							"marginal_rate": bracket["marginal_rate"],
						}
					)
		STORE.seed("Federal Tax Table", brackets)

	def _seed_employees(self):
		STORE.seed(
			"Employee",
			[
				{
					"name": WORKER,
					"employee_name": "Ana Reyes",
					"company": MAIN,
					"status": "Active",
					"date_of_joining": "2025-01-15",
				},
				{
					"name": PICKER,
					"employee_name": "Beto Cruz",
					"company": MAIN,
					"status": "Active",
					"date_of_joining": "2025-03-01",
				},
				{
					"name": DRIVER,
					"employee_name": "Carla Mota",
					"company": MAIN,
					"status": "Active",
					"date_of_joining": "2025-02-01",
				},
			],
		)

	def _seed_state_configs(self):
		STORE.seed(
			"State Tax Configuration",
			[
				{
					"name": "STC-OR-2025",
					"company": MAIN,
					"state": "OR",
					"tax_year": 2025,
					"status": "Active",
					"or_income_tax_enabled": 0,
					"or_transit_tax_rate": 0.1,
					"or_paid_leave_rate": 1.0,
					"or_paid_leave_employee_share": 60,
					"or_paid_leave_employer_share": 40,
					"or_paid_leave_small_employer": 0,
					"or_workers_comp_rate": 1.5,
				},
				{
					"name": "STC-WA-2025",
					"company": MAIN,
					"state": "WA",
					"tax_year": 2025,
					"status": "Active",
					"wa_pfml_rate": 0.92,
					"wa_pfml_employee_share": 72.76,
					"wa_pfml_employer_share": 27.24,
					"wa_pfml_wage_base": 176100,
					"wa_cares_rate": 0.58,
					"wa_cares_employee_only": 1,
					"wa_cares_exempt_employees": "",
					"wa_li_rate_employee": 0.25,
					"wa_li_rate_employer": 0.35,
				},
			],
		)

	# ── helpers ───────────────────────────────────────────────────────

	def structure(self, employee, pay_type="Hourly", rate=20.0, company=MAIN):
		return self.tool_data(
			"create_salary_structure",
			{
				"employee": employee,
				"company": company,
				"pay_type": pay_type,
				"base_rate": rate,
				"effective_from": "2025-01-01",
			},
		)

	def seed_shifts(self, *rows):
		STORE.seed("Farm Shift", list(rows))

	def aggregate(self, shifts, start=PERIOD_START, end=PERIOD_END, **kwargs):
		return pi.aggregate_shifts_for_period(shifts, start, end, **kwargs)

	def install_bucket_log(self, *rows, with_count=True):
		"""Stand the BucketLog bridge up on this site and seed it.

		Registered per test rather than in setUp, because "the bridge is not
		installed" is itself one of the cases under test and the default has to
		be the site that does not have it.
		"""
		fields = [
			{"fieldname": "name", "fieldtype": "Data"},
			{"fieldname": "company", "fieldtype": "Link", "options": "Company"},
			{"fieldname": "picker_id", "fieldtype": "Data"},
			{"fieldname": "logged_at", "fieldtype": "Datetime"},
		]
		if with_count:
			fields.append({"fieldname": "bucket_count", "fieldtype": "Float"})
		register_doctype("Bucket Log Entry", fields)
		if rows:
			STORE.seed("Bucket Log Entry", list(rows))

	def preview(self, **extra):
		return self.tool_data(
			"preview_payroll_for_period",
			{
				"company": MAIN,
				"pay_period_start": PERIOD_START,
				"pay_period_end": PERIOD_END,
				**extra,
			},
		)


# ── Claim 1: the segment is the unit ──────────────────────────────────


class TheSegmentIsTheUnit(IntegrationTestCase):
	"""Hours come from each worker's own span, not the crew's."""

	def test_a_worker_who_joined_late_is_not_paid_for_the_hours_before_they_arrived(self):
		"""The crew worked 06:00 to 15:00. Ana joined at 07:10 and left at 13:00.

		Both are true, both are stored, and the one payroll reads is Ana's.
		"""
		rows = self.aggregate(
			[
				shift(
					"S1",
					"2025-06-02",
					crew=[
						member(WORKER, joined="2025-06-02 07:10:00", left="2025-06-02 13:00:00"),
						member(PICKER),
					],
				),
			]
		)
		self.assertAlmostEqual(rows[WORKER]["total_hours"], 5.83, places=2)
		self.assertEqual(rows[PICKER]["total_hours"], 9.0)

	def test_a_crew_row_with_no_times_inherits_the_shifts(self):
		rows = self.aggregate([shift("S1", "2025-06-02", crew=[member(PICKER)])])
		self.assertEqual(rows[PICKER]["total_hours"], 9.0)

	def test_several_employees_across_several_shifts_and_two_states(self):
		rows = self.aggregate(
			[
				shift("S1", "2025-06-02", state="OR", crew=[member(WORKER), member(PICKER)]),
				shift("S2", "2025-06-03", state="OR", crew=[member(WORKER)]),
				shift("S3", "2025-06-04", state="WA", crew=[member(PICKER), member(DRIVER)]),
			]
		)
		self.assertEqual(sorted(rows), [WORKER, PICKER, DRIVER])
		self.assertEqual(rows[WORKER]["hours_by_state"], {"OR": 18.0})
		self.assertEqual(rows[PICKER]["hours_by_state"], {"OR": 9.0, "WA": 9.0})
		self.assertEqual(rows[DRIVER]["hours_by_state"], {"WA": 9.0})
		self.assertEqual(rows[WORKER]["shift_count"], 2)

	def test_the_employee_name_survives_onto_the_aggregate(self):
		rows = self.aggregate(
			[
				shift("S1", "2025-06-02", crew=[member(WORKER, name="Ana Reyes")]),
			]
		)
		self.assertEqual(rows[WORKER]["employee_name"], "Ana Reyes")

	def test_a_shift_outside_the_period_is_excluded_and_reported(self):
		"""Excluded rather than dropped. A shift the aggregator ignored is a fact
		somebody may need to see when the totals look short."""
		rows = self.aggregate(
			[
				shift("IN", "2025-06-02", crew=[member(WORKER)]),
				shift("BEFORE", "2025-05-30", crew=[member(WORKER)]),
				shift("AFTER", "2025-06-20", crew=[member(WORKER)]),
			]
		)
		self.assertEqual(rows[WORKER]["total_hours"], 9.0)
		names = {row["shift"] for row in rows[WORKER]["shifts_outside_period"]}
		self.assertEqual(names, {"BEFORE", "AFTER"})

	def test_a_flat_single_worker_shift_still_works(self):
		"""The v0.30.0 shape: `employee` and `hours` on the shift itself."""
		rows = self.aggregate(
			[
				{
					"name": "S1",
					"employee": WORKER,
					"work_state": "OR",
					"start_datetime": "2025-06-02 06:00:00",
					"hours": 7.5,
				},
			]
		)
		self.assertEqual(rows[WORKER]["total_hours"], 7.5)


# ── Claim 2: overtime is a weekly question ────────────────────────────


class OvertimeIsWeekly(IntegrationTestCase):
	"""Forty hours in a workweek, walked in time order."""

	def _week(self, days, hours=8.0, state="OR", start_day=2):
		rows = []
		for index in range(days):
			day = f"2025-06-{start_day + index:02d}"
			end_hour = 6 + int(hours)
			minutes = round((hours - int(hours)) * 60)
			rows.append(
				shift(
					f"S{start_day + index}",
					day,
					state=state,
					end=f"{end_hour:02d}:{minutes:02d}:00",
					crew=[member(WORKER)],
				)
			)
		return rows

	def test_thirty_two_hours_is_no_overtime(self):
		rows = self.aggregate(self._week(4, hours=8.0))
		self.assertEqual(rows[WORKER]["total_hours"], 32.0)
		self.assertEqual(rows[WORKER]["overtime_hours"], 0.0)
		self.assertEqual(rows[WORKER]["regular_hours"], 32.0)

	def test_exactly_forty_hours_is_no_overtime(self):
		"""The boundary itself. Forty is the last regular hour, not the first
		overtime one."""
		rows = self.aggregate(self._week(5, hours=8.0))
		self.assertEqual(rows[WORKER]["total_hours"], 40.0)
		self.assertEqual(rows[WORKER]["overtime_hours"], 0.0)

	def test_forty_five_hours_is_five_of_overtime(self):
		rows = self.aggregate(self._week(5, hours=9.0))
		self.assertEqual(rows[WORKER]["total_hours"], 45.0)
		self.assertEqual(rows[WORKER]["overtime_hours"], 5.0)
		self.assertEqual(rows[WORKER]["regular_hours"], 40.0)

	def test_forty_five_then_thirty_five_is_five_of_overtime_not_none(self):
		"""THE TEST THIS RELEASE EXISTS FOR. Eighty hours over a biweekly period
		with forty-five in the first week is five hours of overtime. Comparing
		the period total to eighty finds none of it, which is the arithmetic a
		hand-keyed payroll does."""
		week_one = self._week(5, hours=9.0, start_day=2)
		week_two = self._week(5, hours=7.0, start_day=9)
		rows = self.aggregate(week_one + week_two)
		self.assertEqual(rows[WORKER]["total_hours"], 80.0)
		self.assertEqual(rows[WORKER]["overtime_hours"], 5.0)
		self.assertEqual([w["overtime_hours"] for w in rows[WORKER]["weeks"]], [5.0, 0.0])
		self.assertEqual([w["workweek"] for w in rows[WORKER]["weeks"]], [1, 2])

	def test_the_week_rows_carry_their_own_dates(self):
		rows = self.aggregate(self._week(2, hours=8.0))
		week = rows[WORKER]["weeks"][0]
		self.assertEqual(week["week_start"], "2025-06-02")
		self.assertEqual(week["week_end"], "2025-06-08")

	def test_the_shift_that_crosses_forty_is_split_across_both(self):
		"""Four nine-hour days is thirty-six. The fifth crosses at hour four."""
		rows = self.aggregate(self._week(5, hours=9.0))
		fifth = rows[WORKER]["shifts"][4]
		self.assertEqual(fifth["regular_hours"], 4.0)
		self.assertEqual(fifth["overtime_hours"], 5.0)

	def test_the_overtime_belongs_to_the_state_it_was_worked_in(self):
		"""Monday to Thursday in Oregon at ten hours is forty. Friday is
		Washington, and every hour of it is overtime — Washington's."""
		rows = self.aggregate(
			self._week(4, hours=10.0, state="OR", start_day=2)
			+ self._week(1, hours=8.0, state="WA", start_day=6)
		)
		agg = rows[WORKER]
		self.assertEqual(agg["total_hours"], 48.0)
		self.assertEqual(agg["overtime_hours"], 8.0)
		self.assertEqual(agg["overtime_hours_by_state"], {"OR": 0.0, "WA": 8.0})

	def test_the_threshold_is_an_argument(self):
		rows = self.aggregate(self._week(4, hours=8.0), overtime_threshold=30.0)
		self.assertEqual(rows[WORKER]["overtime_hours"], 2.0)

	def test_the_workweek_anchor_moves_the_boundary(self):
		"""A Sunday-anchored workweek splits the same five nine-hour days into
		two weeks, and five hours of overtime becomes none."""
		days = self._week(5, hours=9.0, start_day=2)
		anchored = self.aggregate(days, workweek_anchor="2025-05-29")
		self.assertEqual(anchored[WORKER]["total_hours"], 45.0)
		self.assertEqual(len(anchored[WORKER]["weeks"]), 2)
		self.assertEqual(anchored[WORKER]["overtime_hours"], 0.0)


# ── Claim 3: two kinds of break ───────────────────────────────────────


class BreaksAreTwoKinds(IntegrationTestCase):
	"""Paid rest stays on the clock; the unpaid meal comes off it."""

	def test_an_unpaid_meal_comes_off_the_span(self):
		rows = self.aggregate(
			[
				shift("S1", "2025-06-02", crew=[member(WORKER)], unpaid_break_hours=0.5),
			]
		)
		self.assertEqual(rows[WORKER]["total_hours"], 8.5)
		self.assertEqual(rows[WORKER]["unpaid_break_hours"], 0.5)

	def test_a_paid_rest_break_stays_inside_the_hours(self):
		rows = self.aggregate(
			[
				shift("S1", "2025-06-02", crew=[member(WORKER)], break_hours=0.25),
			]
		)
		self.assertEqual(rows[WORKER]["total_hours"], 9.0)
		self.assertEqual(rows[WORKER]["break_hours"], 0.25)

	def test_an_unpaid_meal_does_not_count_toward_overtime(self):
		"""Five nine-hour shifts with a half-hour meal each is 42.5 hours, so 2.5
		of overtime rather than five. The meal is not hours worked."""
		days = [
			shift(f"S{d}", f"2025-06-{d:02d}", crew=[member(WORKER)], unpaid_break_hours=0.5)
			for d in range(2, 7)
		]
		rows = self.aggregate(days)
		self.assertEqual(rows[WORKER]["total_hours"], 42.5)
		self.assertEqual(rows[WORKER]["overtime_hours"], 2.5)

	def test_a_crew_row_overrides_the_shifts_breaks(self):
		rows = self.aggregate(
			[
				shift(
					"S1",
					"2025-06-02",
					unpaid_break_hours=0.5,
					crew=[
						member(WORKER),
						member(PICKER, unpaid_break_hours=0.0),
					],
				),
			]
		)
		self.assertEqual(rows[WORKER]["total_hours"], 8.5)
		self.assertEqual(rows[PICKER]["total_hours"], 9.0)

	def test_a_break_longer_than_the_shift_is_capped_at_the_shift(self):
		"""The clock is the harder fact. A rest period recorded as longer than
		the span it sat in is a data entry error, and paying it at face value
		would pay somebody for time the record says they were not there."""
		rows = self.aggregate(
			[
				shift("S1", "2025-06-02", end="08:00:00", break_hours=5.0, crew=[member(WORKER)]),
			]
		)
		self.assertEqual(rows[WORKER]["total_hours"], 2.0)
		self.assertEqual(rows[WORKER]["break_hours"], 2.0)

	def test_break_pay_reaches_a_piece_rate_slip_at_the_average_hourly(self):
		"""Eight paid hours of which half an hour is rest: 7.5 picking hours,
		300 buckets at $1 is $300, so the average is $40/h and the rest break is
		worth $20. WAC 296-131-020, computed from the shift rather than typed."""
		agg = self.aggregate(
			[
				shift(
					"S1",
					"2025-06-02",
					end="14:00:00",
					break_hours=0.5,
					crew=[member(WORKER, piece_units=300)],
				),
			]
		)[WORKER]
		slip = calculate_full_payroll(
			{"employee": WORKER},
			pi.engine_shift_rows(agg),
			{"pay_type": "Piece Rate", "base_rate": 1.0, "name": "FSS-1"},
			{"pay_frequency": "Biweekly", "w4_data": {}, "fica_config": {}},
		)
		self.assertEqual(slip["gross_detail"]["piece_earnings"], 300.0)
		self.assertEqual(slip["gross_detail"]["break_pay"], 20.0)
		self.assertEqual(slip["gross_pay"], 320.0)


# ── Claim 4: piece units reach the payroll ────────────────────────────


class PieceUnitsReachThePayroll(IntegrationTestCase):
	"""Three ways in, one place they land."""

	def test_units_on_the_crew_row(self):
		rows = self.aggregate(
			[
				shift("S1", "2025-06-02", crew=[member(WORKER, piece_units=120)]),
			]
		)
		self.assertEqual(rows[WORKER]["piece_units"], 120.0)

	def test_units_in_a_per_employee_map(self):
		rows = self.aggregate(
			[
				shift(
					"S1",
					"2025-06-02",
					crew=[member(WORKER), member(PICKER)],
					piece_units_by_employee={WORKER: 90, PICKER: 140},
				),
			]
		)
		self.assertEqual(rows[WORKER]["piece_units"], 90.0)
		self.assertEqual(rows[PICKER]["piece_units"], 140.0)

	def test_units_from_attached_piece_rows(self):
		rows = self.aggregate(
			[
				shift(
					"S1",
					"2025-06-02",
					crew=[member(WORKER)],
					piece_rows=[
						{"picker_id": WORKER, "piece_units": 30},
						{"picker_id": WORKER},  # a bucket log with no count IS one bucket
						{"picker_id": PICKER, "piece_units": 99},
					],
				),
			]
		)
		self.assertEqual(rows[WORKER]["piece_units"], 31.0)

	def test_the_three_sources_add_rather_than_shadow_each_other(self):
		"""A farm that changes how it records buckets mid-season should be paid
		for both halves, not for whichever the code checked first."""
		rows = self.aggregate(
			[
				shift(
					"S1",
					"2025-06-02",
					crew=[member(WORKER, piece_units=10)],
					piece_units_by_employee={WORKER: 20},
					piece_rows=[{"employee": WORKER, "units": 5}],
				),
			]
		)
		self.assertEqual(rows[WORKER]["piece_units"], 35.0)

	def test_units_are_split_by_state_like_the_hours(self):
		rows = self.aggregate(
			[
				shift("S1", "2025-06-02", state="OR", crew=[member(WORKER, piece_units=100)]),
				shift("S2", "2025-06-03", state="WA", crew=[member(WORKER, piece_units=60)]),
			]
		)
		self.assertEqual(rows[WORKER]["piece_units_by_state"], {"OR": 100.0, "WA": 60.0})

	def test_a_piece_rate_run_turns_bucket_logs_into_gross(self):
		"""Two nine-hour days, 200 buckets each, $1.50 a bucket: $600. The hours
		are the shift's and the buckets are the bridge's, joined on the day."""
		self.structure(WORKER, pay_type="Piece Rate", rate=1.50)
		self.seed_shifts(
			shift("S1", "2025-06-02", crew=[member(WORKER)]),
			shift("S2", "2025-06-03", crew=[member(WORKER)]),
		)
		self.install_bucket_log(
			bucket("BL1", WORKER, "2025-06-02", 200),
			bucket("BL2", WORKER, "2025-06-03", 200),
		)
		slip = next(s for s in self.preview()["slips"] if s["employee"] == WORKER)
		self.assertEqual(slip["pay_type"], "Piece Rate")
		self.assertEqual(slip["piece_units"], 400.0)
		self.assertEqual(slip["total_hours"], 18.0)
		self.assertEqual(slip["gross_pay"], 600.0)

	def test_a_bucket_log_with_no_count_column_is_one_bucket_a_row(self):
		"""The bridge is written by whichever version of the iPad app is in the
		field, and a row with no count IS the bucket. Reading it as zero would
		pay somebody nothing for a day of picking."""
		self.structure(WORKER, pay_type="Piece Rate", rate=2.00)
		self.seed_shifts(shift("S1", "2025-06-02", crew=[member(WORKER)]))
		self.install_bucket_log(
			bucket("BL1", WORKER, "2025-06-02"),
			bucket("BL2", WORKER, "2025-06-02"),
			bucket("BL3", WORKER, "2025-06-02"),
			with_count=False,
		)
		data = self.preview()
		slip = next(s for s in data["slips"] if s["employee"] == WORKER)
		self.assertEqual(slip["piece_units"], 3.0)
		self.assertEqual(slip["gross_pay"], 6.0)
		self.assertIn("ONE unit", " ".join(data["sources"]["notes"]))

	def test_buckets_on_a_day_with_no_shift_are_still_paid_and_reported(self):
		"""Dropping them because the shift register has a hole in it would pay
		somebody nothing for a day they worked. They carry no hours, so they add
		nothing to overtime — and the result says how many did that."""
		self.structure(WORKER, pay_type="Piece Rate", rate=1.00)
		self.seed_shifts(shift("S1", "2025-06-02", crew=[member(WORKER)]))
		self.install_bucket_log(
			bucket("BL1", WORKER, "2025-06-02", 100),
			bucket("BL2", WORKER, "2025-06-05", 40),
		)
		data = self.preview()
		slip = next(s for s in data["slips"] if s["employee"] == WORKER)
		self.assertEqual(slip["piece_units"], 140.0)
		self.assertEqual(slip["total_hours"], 9.0)
		self.assertEqual(data["sources"]["piece_rows_without_a_shift"], 1)
		self.assertIn("no shift on", " ".join(data["sources"]["notes"]))

	def test_a_crew_row_column_is_read_where_a_site_has_added_one(self):
		"""Farm Shift Crew Member records who and when, not how much. A site that
		adds the column is recording the right thing in the right place."""
		add_field("Farm Shift Crew Member", "piece_units", "Float")
		self.structure(WORKER, pay_type="Piece Rate", rate=1.50)
		self.seed_shifts(
			shift("S1", "2025-06-02", crew=[member(WORKER, piece_units=180)]),
		)
		slip = next(s for s in self.preview()["slips"] if s["employee"] == WORKER)
		self.assertEqual(slip["piece_units"], 180.0)
		self.assertEqual(slip["gross_pay"], 270.0)


# ── Claim 5: the cross-state worker ───────────────────────────────────


class TheCrossStateWorker(IntegrationTestCase):
	"""OR shifts and WA shifts in one period, taxed in both."""

	def setUp(self):
		super().setUp()
		self.structure(WORKER, pay_type="Hourly", rate=20.0)
		self.seed_shifts(
			shift("OR1", "2025-06-02", state="OR", crew=[member(WORKER)]),
			shift("OR2", "2025-06-03", state="OR", crew=[member(WORKER)]),
			shift("WA1", "2025-06-04", state="WA", crew=[member(WORKER)]),
		)
		self.slip = next(
			s
			for s in self.tool_data(
				"preview_payroll_for_period",
				{
					"company": MAIN,
					"pay_period_start": PERIOD_START,
					"pay_period_end": PERIOD_END,
				},
			)["slips"]
			if s["employee"] == WORKER
		)

	def test_the_hours_are_split_by_state(self):
		self.assertEqual(self.slip["hours_by_state"], {"OR": 18.0, "WA": 9.0})
		self.assertEqual(self.slip["total_hours"], 27.0)

	def test_the_wages_are_allocated_by_the_hours(self):
		"""$20 an hour over 27 hours is $540, two thirds of it Oregon's."""
		self.assertEqual(self.slip["gross_pay"], 540.0)
		self.assertEqual(self.slip["state_wages"], {"OR": 360.0, "WA": 180.0})

	def test_both_states_withheld_something(self):
		detail = self.slip["state_withholding"]
		self.assertGreater(detail, 0)

	def test_the_primary_state_is_where_most_of_the_hours_were(self):
		self.assertEqual(self.slip["work_state"], "OR")


# ── Claim 6: minimum wage, per state ──────────────────────────────────


class MinimumWageIsPerState(IntegrationTestCase):
	"""Each state's own floor, against the real shift hours."""

	def test_a_piece_rate_day_below_oregons_floor_is_named_and_priced(self):
		"""Ten hours, fifty buckets at a dollar. $5 an hour against Oregon's
		$14.70 is a $97 shortfall, and it is REPORTED rather than paid."""
		result = pi.check_minimum_wage_by_state({"OR": 10.0}, {"OR": 50.0})
		self.assertFalse(result["meets_minimum_wage"])
		self.assertEqual(result["states_below_minimum"], ["OR"])
		self.assertEqual(result["total_shortfall"], 97.0)
		self.assertEqual(result["by_state"]["OR"]["effective_hourly_rate"], 5.0)

	def test_a_compliant_washington_week_does_not_paper_over_an_oregon_one(self):
		"""THE REASON IT IS PER STATE. Averaged across both the worker clears
		$16.68 an hour and looks fine; Oregon's ten hours earned $5 an hour."""
		result = pi.check_minimum_wage_by_state(
			{"OR": 10.0, "WA": 30.0},
			{"OR": 50.0, "WA": 1000.0},
		)
		self.assertEqual(result["states_below_minimum"], ["OR"])
		self.assertTrue(result["by_state"]["WA"]["meets_minimum_wage"])

	def test_the_portland_region_raises_the_floor(self):
		passing = pi.check_minimum_wage_by_state({"OR": 10.0}, {"OR": 150.0})
		metro = pi.check_minimum_wage_by_state(
			{"OR": 10.0},
			{"OR": 150.0},
			{"OR": "portland_metro"},
		)
		self.assertTrue(passing["meets_minimum_wage"])
		self.assertFalse(metro["meets_minimum_wage"])
		self.assertEqual(metro["by_state"]["OR"]["shortfall"], 9.5)

	def test_the_shortfall_is_not_added_to_gross(self):
		"""Reported, never remedied. A payroll engine that quietly topped pay up
		would hide the fact that a piece rate is set too low to be lawful."""
		self.structure(PICKER, pay_type="Piece Rate", rate=1.0)
		self.seed_shifts(
			shift("S1", "2025-06-02", end="16:00:00", crew=[member(PICKER)]),
		)
		self.install_bucket_log(bucket("BL1", PICKER, "2025-06-02", 50))
		data = self.preview()
		slip = next(s for s in data["slips"] if s["employee"] == PICKER)
		self.assertEqual(slip["total_hours"], 10.0)
		self.assertEqual(slip["gross_pay"], 50.0)
		self.assertFalse(slip["minimum_wage_detail"]["meets_minimum_wage"])
		self.assertEqual(slip["minimum_wage_detail"]["total_shortfall"], 97.0)
		self.assertEqual(data["totals"]["below_minimum_wage"][0]["employee"], PICKER)

	def test_a_stateless_shift_does_not_invent_a_failure(self):
		"""A shift with no work_state has no floor to fall below. Reporting one
		would be reporting a violation of a law nobody named."""
		result = pi.check_minimum_wage_by_state({"": 10.0}, {})
		self.assertTrue(result["meets_minimum_wage"])


# ── Claim 7: end to end ───────────────────────────────────────────────


class EndToEnd(IntegrationTestCase):
	"""Shifts on the site, through the tools, into a payroll entry."""

	def setUp(self):
		super().setUp()
		self.structure(WORKER, pay_type="Hourly", rate=20.0)
		self.structure(PICKER, pay_type="Piece Rate", rate=1.50)
		self.tool_data(
			"submit_w4",
			{
				"employee": WORKER,
				"company": MAIN,
				"tax_year": 2025,
				"filing_status": "Single or Married Filing Separately",
			},
		)
		# Week one: Ana works five nines (45h — five of overtime). Beto picks two
		# days at 250 buckets.
		self.seed_shifts(
			*[shift(f"S{d}", f"2025-06-{d:02d}", crew=[member(WORKER)]) for d in range(2, 7)],
			shift("P1", "2025-06-09", crew=[member(PICKER)]),
			shift("P2", "2025-06-10", crew=[member(PICKER)]),
		)
		self.install_bucket_log(
			bucket("BL1", PICKER, "2025-06-09", 250),
			bucket("BL2", PICKER, "2025-06-10", 250),
		)

	def test_the_preview_computes_both_workers_off_the_register(self):
		data = self.preview()
		ana = next(s for s in data["slips"] if s["employee"] == WORKER)
		beto = next(s for s in data["slips"] if s["employee"] == PICKER)

		self.assertEqual(ana["total_hours"], 45.0)
		self.assertEqual(ana["overtime_hours"], 5.0)
		# 40 regular at $20 plus 5 overtime at $30.
		self.assertEqual(ana["gross_pay"], 950.0)

		self.assertEqual(beto["piece_units"], 500.0)
		self.assertEqual(beto["gross_pay"], 750.0)

	def test_the_preview_writes_nothing(self):
		before = len(STORE.rows("Farm Payroll Entry"))
		data = self.preview()
		self.assertIsNone(data["created"])
		self.assertEqual(len(STORE.rows("Farm Payroll Entry")), before)

	def test_the_run_writes_a_calculated_entry_whose_slips_match_the_preview(self):
		preview = self.preview()
		run = self.tool_data(
			"run_payroll_for_period",
			{
				"company": MAIN,
				"pay_period_start": PERIOD_START,
				"pay_period_end": PERIOD_END,
			},
		)
		self.assertEqual(run["status"], "Calculated")
		self.assertEqual(run["totals"]["total_gross"], preview["totals"]["total_gross"])
		self.assertEqual(run["totals"]["total_net"], preview["totals"]["total_net"])

		entry = self.tool_data("get_payroll_entry", {"name": run["name"]})
		self.assertEqual(entry["status"], "Calculated")
		by_employee = {slip["employee"]: slip for slip in entry["slips"]}
		self.assertEqual(by_employee[WORKER]["overtime_hours"], 5.0)
		self.assertEqual(by_employee[WORKER]["gross_pay"], 950.0)
		self.assertEqual(by_employee[PICKER]["piece_units"], 500.0)

	def test_a_calculated_run_can_be_submitted(self):
		run = self.tool_data(
			"run_payroll_for_period",
			{
				"company": MAIN,
				"pay_period_start": PERIOD_START,
				"pay_period_end": PERIOD_END,
			},
		)
		data = self.tool_data("submit_payroll", {"name": run["name"]})
		self.assertEqual(data["status"], "Submitted")

	def test_the_timesheet_summary_agrees_with_the_slip(self):
		summary = self.tool_data(
			"get_employee_timesheet_summary",
			{
				"employee": WORKER,
				"start_date": PERIOD_START,
				"end_date": PERIOD_END,
			},
		)
		self.assertEqual(summary["total_hours"], 45.0)
		self.assertEqual(summary["overtime_hours"], 5.0)
		self.assertEqual(summary["shift_count"], 5)
		self.assertEqual(summary["salary_structure"]["pay_type"], "Hourly")
		self.assertEqual(summary["employee_name"], "Ana Reyes")

	def test_the_v0_30_0_single_employee_preview_now_sees_the_same_hours(self):
		"""The old tool, rewired. Before v0.35.0 it reported the crew span with
		no overtime; the number it gives now is the number the run gives."""
		data = self.tool_data(
			"preview_payroll",
			{
				"employee": WORKER,
				"pay_period_start": PERIOD_START,
				"pay_period_end": PERIOD_END,
				"company": MAIN,
			},
		)
		self.assertEqual(data["total_hours"], 45.0)
		self.assertEqual(data["overtime_hours"], 5.0)
		self.assertEqual(data["gross_pay"], 950.0)

	def test_the_run_is_limited_to_one_employee_when_asked(self):
		run = self.tool_data(
			"run_payroll_for_period",
			{
				"company": MAIN,
				"pay_period_start": PERIOD_START,
				"pay_period_end": PERIOD_END,
				"employee": PICKER,
			},
		)
		self.assertEqual([s["employee"] for s in run["slips"]], [PICKER])

	def test_a_second_period_carries_the_first_periods_ytd(self):
		"""The Social Security wage base is an annual per-person cap, so a run
		that could not see the periods before it would restart it."""
		self.tool_data(
			"run_payroll_for_period",
			{
				"company": MAIN,
				"pay_period_start": PERIOD_START,
				"pay_period_end": PERIOD_END,
			},
		)
		ytd = self.tool_data(
			"preview_payroll_for_period",
			{
				"company": MAIN,
				"pay_period_start": "2025-06-16",
				"pay_period_end": "2025-06-29",
			},
		)
		self.assertEqual(ytd["totals"]["total_hours"], 0.0)
		self.assertEqual(ytd["pay_period_start"], "2025-06-16")

	def test_the_detail_flag_adds_the_timesheet_behind_the_figure(self):
		plain = self.preview()["slips"][0]
		detailed = self.preview(detail=1)["slips"][0]
		self.assertNotIn("timesheet", plain)
		self.assertIn("timesheet", detailed)
		self.assertTrue(detailed["timesheet"])


# ── Claim 8: edge cases ───────────────────────────────────────────────


class EdgeCases(IntegrationTestCase):
	"""No shifts, a partial week, no structure, an unclosed shift."""

	def test_no_shifts_at_all_still_pays_a_salary(self):
		self.structure(DRIVER, pay_type="Salary", rate=2400.0)
		data = self.preview()
		slip = next(s for s in data["slips"] if s["employee"] == DRIVER)
		self.assertEqual(slip["total_hours"], 0.0)
		self.assertEqual(slip["gross_pay"], 2400.0)

	def test_no_shifts_and_hourly_is_a_zero_slip_rather_than_no_slip(self):
		self.structure(WORKER, pay_type="Hourly", rate=20.0)
		data = self.preview()
		slip = next(s for s in data["slips"] if s["employee"] == WORKER)
		self.assertEqual(slip["gross_pay"], 0.0)

	def test_include_unworked_off_leaves_the_unworked_out(self):
		self.structure(WORKER, pay_type="Hourly", rate=20.0)
		data = self.tool_data(
			"preview_payroll_for_period",
			{
				"company": MAIN,
				"pay_period_start": PERIOD_START,
				"pay_period_end": PERIOD_END,
				"include_unworked": 0,
			},
		)
		self.assertEqual(data["slips"], [])

	def test_a_partial_week_is_not_overtime(self):
		self.structure(WORKER, pay_type="Hourly", rate=20.0)
		self.seed_shifts(*[shift(f"S{d}", f"2025-06-{d:02d}", crew=[member(WORKER)]) for d in (13, 14, 15)])
		data = self.preview()
		slip = next(s for s in data["slips"] if s["employee"] == WORKER)
		self.assertEqual(slip["total_hours"], 27.0)
		self.assertEqual(slip["overtime_hours"], 0.0)

	def test_a_worker_with_no_salary_structure_is_named_not_zeroed(self):
		self.structure(WORKER, pay_type="Hourly", rate=20.0)
		self.seed_shifts(
			shift("S1", "2025-06-02", crew=[member(WORKER), member(PICKER)]),
		)
		data = self.preview()
		self.assertEqual([s["employee"] for s in data["slips"]], [WORKER])
		missing = data["employees_missing_structures"]
		self.assertEqual([row["employee"] for row in missing], [PICKER])
		self.assertEqual(missing[0]["employee_name"], "Beto Cruz")
		self.assertEqual(missing[0]["total_hours"], 9.0)

	def test_a_run_where_nobody_can_be_paid_refuses_and_says_who(self):
		self.seed_shifts(shift("S1", "2025-06-02", crew=[member(PICKER)]))
		error = self.tool_error(
			"run_payroll_for_period",
			{
				"company": MAIN,
				"pay_period_start": PERIOD_START,
				"pay_period_end": PERIOD_END,
			},
		)
		self.assertIn("Beto Cruz", error)
		self.assertIn("create_salary_structure", error)
		self.assertEqual(STORE.rows("Farm Payroll Entry"), [])

	def test_an_unclosed_shift_counts_no_hours_and_says_so(self):
		"""A shift nobody ended has no span, so it is worth zero hours. That is
		arithmetic nobody would question, which is exactly why it is reported."""
		self.structure(WORKER, pay_type="Hourly", rate=20.0)
		self.seed_shifts(
			shift("OPEN", "2025-06-02", end=None, status="Active", crew=[member(WORKER)]),
		)
		data = self.preview()
		slip = next(s for s in data["slips"] if s["employee"] == WORKER)
		self.assertEqual(slip["total_hours"], 0.0)
		self.assertEqual(slip["open_shifts"], ["OPEN"])
		self.assertEqual(data["totals"]["with_open_shifts"][0]["employee"], WORKER)

	def test_a_cancelled_shift_is_not_counted(self):
		self.structure(WORKER, pay_type="Hourly", rate=20.0)
		self.seed_shifts(
			shift("GOOD", "2025-06-02", crew=[member(WORKER)]),
			shift("GONE", "2025-06-03", cancelled=1, status="Cancelled", crew=[member(WORKER)]),
		)
		data = self.preview()
		slip = next(s for s in data["slips"] if s["employee"] == WORKER)
		self.assertEqual(slip["total_hours"], 9.0)
		self.assertIn("cancelled", " ".join(data["sources"]["notes"]))

	def test_another_companys_shifts_are_not_on_this_companys_payroll(self):
		self.structure(WORKER, pay_type="Hourly", rate=20.0)
		self.seed_shifts(
			shift("MINE", "2025-06-02", crew=[member(WORKER)]),
			shift("THEIRS", "2025-06-03", company=OTHER, crew=[member(WORKER)]),
		)
		data = self.preview()
		slip = next(s for s in data["slips"] if s["employee"] == WORKER)
		self.assertEqual(slip["total_hours"], 9.0)

	def test_backwards_dates_are_refused_before_anything_is_written(self):
		error = self.tool_error(
			"run_payroll_for_period",
			{
				"company": MAIN,
				"pay_period_start": PERIOD_END,
				"pay_period_end": PERIOD_START,
			},
		)
		self.assertIn("before", error)
		self.assertEqual(STORE.rows("Farm Payroll Entry"), [])

	def test_an_unknown_pay_frequency_is_refused_by_name(self):
		error = self.tool_error(
			"preview_payroll_for_period",
			{
				"company": MAIN,
				"pay_period_start": PERIOD_START,
				"pay_period_end": PERIOD_END,
				"pay_frequency": "Fortnightly",
			},
		)
		self.assertIn("Biweekly", error)

	def test_a_timesheet_summary_for_somebody_with_no_structure_says_so(self):
		summary = self.tool_data(
			"get_employee_timesheet_summary",
			{
				"employee": PICKER,
				"start_date": PERIOD_START,
				"end_date": PERIOD_END,
			},
		)
		self.assertIsNone(summary["salary_structure"])
		self.assertIn("create_salary_structure", summary["no_salary_structure"])

	def test_the_result_says_where_the_piece_units_did_not_come_from(self):
		"""Bucket Log Entry ships with erpnext_mcp as of v0.44.0, so it is always
		queried — a run with no captures in the period has produced zeros
		because nobody's bucket fell in it, not because the source is absent."""
		self.structure(WORKER, pay_type="Piece Rate", rate=1.0)
		self.seed_shifts(shift("S1", "2025-06-02", crew=[member(WORKER)]))
		data = self.preview()
		self.assertIn("Bucket Log Entry", data["sources"]["sources"])
		notes = " ".join(data["sources"]["notes"])
		self.assertIn("only Accepted captures were counted", notes)
		slip = next(s for s in data["slips"] if s["employee"] == WORKER)
		self.assertEqual(slip["piece_units"], 0.0)

	def test_an_empty_aggregate_is_a_real_aggregate(self):
		agg = pi.empty_aggregate(WORKER, "Ana Reyes")
		self.assertEqual(agg["total_hours"], 0.0)
		self.assertEqual(agg["hours_by_state"], {})
		self.assertEqual(agg["weeks"], [])

	def test_build_payroll_inputs_skips_the_structureless_rather_than_guessing(self):
		aggregates = pi.aggregate_shifts_for_period(
			[shift("S1", "2025-06-02", crew=[member(WORKER), member(PICKER)])],
			PERIOD_START,
			PERIOD_END,
		)
		inputs = pi.build_payroll_inputs(
			aggregates,
			{WORKER: {"name": "FSS-1", "pay_type": "Hourly", "base_rate": 20.0}},
			{},
			{},
			{},
		)
		self.assertEqual([row["employee"] for row in inputs], [WORKER])
		missing = pi.employees_missing_structures(
			aggregates,
			{WORKER: {"name": "FSS-1", "pay_type": "Hourly", "base_rate": 20.0}},
		)
		self.assertEqual([row["employee"] for row in missing], [PICKER])

	def test_a_w4_absent_falls_back_to_single_no_adjustments(self):
		aggregates = pi.aggregate_shifts_for_period(
			[shift("S1", "2025-06-02", crew=[member(WORKER)])],
			PERIOD_START,
			PERIOD_END,
		)
		inputs = pi.build_payroll_inputs(
			aggregates,
			[{"employee": WORKER, "name": "FSS-1", "pay_type": "Hourly", "base_rate": 20.0}],
			{},
			{},
			{},
		)
		self.assertEqual(inputs[0]["tax_config"]["w4_data"]["filing_status"], "Single")


# ── The switches ──────────────────────────────────────────────────────


class TheTools(IntegrationTestCase):
	"""Two reads on by default, the run off by default, each refused by name."""

	def test_the_two_reads_are_on_by_default_and_the_run_is_not(self):
		from erpnext_mcp import registry

		fields = {field["fieldname"]: field for field in self.settings_meta()["fields"]}
		self.assertEqual(fields["allow_get_employee_timesheet_summary"]["default"], "1")
		self.assertEqual(fields["allow_preview_payroll_for_period"]["default"], "1")
		self.assertEqual(fields["allow_run_payroll_for_period"]["default"], "0")
		self.assertIn("get_employee_timesheet_summary", registry.READ_TOOLS)
		self.assertIn("preview_payroll_for_period", registry.READ_TOOLS)
		self.assertIn("run_payroll_for_period", registry.MUTATING_TOOLS)

	def settings_meta(self):
		import json
		import pathlib

		path = (
			pathlib.Path(__file__).resolve().parents[1]
			/ "erpnext_mcp"
			/ "erpnext_mcp"
			/ "doctype"
			/ "erpnext_mcp_settings"
			/ "erpnext_mcp_settings.json"
		)
		return json.loads(path.read_text())

	def test_each_tool_is_refused_by_name_when_its_switch_is_off(self):
		for tool in (
			"get_employee_timesheet_summary",
			"preview_payroll_for_period",
			"run_payroll_for_period",
		):
			with self.subTest(tool=tool):
				self.configure(enabled=1, **{**ON, f"allow_{tool}": 0})
				error = self.tool_error(
					tool,
					{
						"company": MAIN,
						"employee": WORKER,
						"pay_period_start": PERIOD_START,
						"pay_period_end": PERIOD_END,
					},
				)
				self.assertIn(tool, error)

	def test_the_kill_switch_stops_all_three(self):
		self.configure(enabled=0, **ON)
		for tool in (
			"get_employee_timesheet_summary",
			"preview_payroll_for_period",
			"run_payroll_for_period",
		):
			with self.subTest(tool=tool):
				_body, status = self.call(
					"tools/call",
					{
						"name": tool,
						"arguments": {
							"company": MAIN,
							"employee": WORKER,
							"pay_period_start": PERIOD_START,
							"pay_period_end": PERIOD_END,
						},
					},
				)
				self.assertEqual(status, 404)
