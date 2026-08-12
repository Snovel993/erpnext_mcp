# SPDX-License-Identifier: MIT
"""The two company-wide wage tables — v0.61.0.

`wage_defaults.py` is pure, the same discipline `model_registry.py` and
`budget_engine.py` keep: it takes rows and returns answers and touches no
database, so the lookup order that decides what a picker is paid can be asserted
without a bench. `tools/wagedefaults.py` is the impure half and the only place
either doctype is read or written. Both halves are here, because the interesting
failure — a rate that resolves to nothing and is paid as the minimum wage makeup
— crosses between them.

SIX CLAIMS.

1. `TheLookupOrder` — the structure's own `base_rate` wins where it is greater
   than zero, the company table answers where it is not, and a miss is a REFUSAL
   naming the company and the activity rather than a zero. Also: an activity is
   compared case-folded with spaces and hyphens as underscores, one unambiguous
   company rate is used where a structure names none, and several are refused
   with the candidates listed.

2. `ChoosingTheRowInForce` — `is_active` beats the dates, an absent `on_date`
   means "whatever is active", a closed window excludes, and where two rows both
   cover a date THE LATER `effective_from` WINS — which is what makes a raise one
   insert rather than an insert and an edit.

3. `Validation` — what each table refuses, as sentences, before anything is
   written.

4. `PieceworkRateTools` — create/list/get/update through the dispatcher: the
   register separates history from what is in force, a raise names what it
   superseded, an edit to a live rate warns that a re-run will now differ,
   `company` and `activity` are locked, retiring is `is_active` and there is no
   delete, and a second ACTIVE row starting the same day is refused.

5. `PositionWageDefaultTools` — the same surface over job titles, plus the
   Designation refusal, and the promise that editing a default reaches nothing
   it already seeded.

6. `PayrollReadsTheTable` — `create_salary_structure` accepts `base_rate` 0 on a
   Piece Rate structure and checks AT CREATION that the inheritance resolves;
   `get_salary_structure` reports `effective_rate` and `rate_source`;
   `list_salary_structures` flags the inheritors; a single-employee preview
   RAISES a missing rate and a whole-company run REPORTS it and pays everybody
   else.
"""

import unittest

from erpnext_mcp import wage_defaults
from erpnext_mcp.errors import ToolError

from .fixtures import MAIN, OTHER, V12TestCase
from .harness import STORE, register_doctype
from .test_payroll import PayrollTestCase

WAGE_TOOLS_ON = {
	f"allow_{name}": 1
	for name in (
		"create_piecework_rate",
		"list_piecework_rates",
		"get_piecework_rate",
		"update_piecework_rate",
		"create_position_wage_default",
		"list_position_wage_defaults",
		"get_position_wage_default",
		"update_position_wage_default",
	)
}

ACTIVITY = "bucket_segmentation"


def _rate(**overrides):
	"""One Piecework Rate row, as the loaders hand it to the pure module."""
	row = {
		"name": "PWR-2026-0001",
		"company": MAIN,
		"activity": ACTIVITY,
		"rate_per_unit": 1.25,
		"effective_from": "2026-01-01",
		"effective_to": None,
		"is_active": 1,
		"modified": "2026-01-01 09:00:00",
	}
	row.update(overrides)
	return row


def _structure(**overrides):
	row = {
		"name": "FSS-0001",
		"employee": "HR-EMP-00002",
		"employee_name": "Piece Worker",
		"company": MAIN,
		"pay_type": "Piece Rate",
		"base_rate": 0.0,
		"piecework_activity": "",
	}
	row.update(overrides)
	return row


# ── Claim 1: the lookup order ──────────────────────────────────────────


class TheLookupOrder(unittest.TestCase):
	"""Structure rate, then company rate, then refuse."""

	def test_a_rate_on_the_structure_wins(self):
		"""A rate negotiated with one person is the more specific record, and a
		company table cannot know about it."""
		answer = wage_defaults.resolve_piece_rate(
			_structure(base_rate=1.75, piecework_activity=ACTIVITY), [_rate(rate_per_unit=1.25)]
		)
		self.assertEqual(answer["rate"], 1.75)
		self.assertEqual(answer["source"], wage_defaults.SOURCE_STRUCTURE)
		self.assertEqual(answer["piecework_rate"], "")

	def test_zero_is_not_a_rate_and_reaches_the_table(self):
		"""`base_rate` is a required Currency field, so a structure created
		without one holds 0.0 and not None. A test for None would never fire."""
		answer = wage_defaults.resolve_piece_rate(
			_structure(base_rate=0.0, piecework_activity=ACTIVITY), [_rate()]
		)
		self.assertEqual(answer["rate"], 1.25)
		self.assertEqual(answer["source"], wage_defaults.SOURCE_COMPANY)
		self.assertEqual(answer["piecework_rate"], "PWR-2026-0001")

	def test_the_desk_and_the_ipad_spell_the_same_activity(self):
		"""A rate table that paid one and not the other would be a rate table
		that failed on a capital letter."""
		answer = wage_defaults.resolve_piece_rate(
			_structure(piecework_activity="Bucket-Segmentation"),
			[_rate(activity="Bucket Segmentation")],
		)
		self.assertEqual(answer["rate"], 1.25)
		self.assertEqual(answer["activity"], ACTIVITY)

	def test_one_unambiguous_company_rate_is_used_where_a_structure_names_none(self):
		"""Not a guess: a company with one piecework rate in force has already
		answered the question."""
		answer = wage_defaults.resolve_piece_rate(_structure(), [_rate()])
		self.assertEqual(answer["rate"], 1.25)
		self.assertEqual(answer["activity"], ACTIVITY)

	def test_several_rates_and_no_activity_is_refused_with_the_candidates(self):
		"""'Set piecework_activity to one of: …' is an instruction somebody can
		act on; 'no rate found' is not."""
		rows = [_rate(), _rate(name="PWR-2026-0002", activity="thinning", rate_per_unit=0.9)]
		with self.assertRaises(ToolError) as caught:
			wage_defaults.resolve_piece_rate(_structure(), rows)
		message = str(caught.exception)
		self.assertIn("piecework_activity", message)
		self.assertIn(ACTIVITY, message)
		self.assertIn("thinning", message)

	def test_no_rate_at_all_refuses_and_names_the_company(self):
		"""THE REFUSAL IS THE POINT. Paying at zero earns the minimum wage
		makeup: the slip balances, the run reports no failure, and the only
		symptom is a number that looks like a rate set too low."""
		with self.assertRaises(ToolError) as caught:
			wage_defaults.resolve_piece_rate(_structure(piecework_activity=ACTIVITY), [])
		message = str(caught.exception)
		self.assertIn("Piece Worker", message)
		self.assertIn(MAIN, message)
		self.assertIn("create_piecework_rate", message)

	def test_the_wrong_activity_is_a_miss_and_says_what_there_is(self):
		with self.assertRaises(ToolError) as caught:
			wage_defaults.resolve_piece_rate(_structure(piecework_activity="pruning"), [_rate()])
		message = str(caught.exception)
		self.assertIn("pruning", message)
		self.assertIn(ACTIVITY, message)

	def test_the_batch_message_and_the_raised_one_are_the_same_sentence(self):
		"""`no_rate_message` is separate because the batch run reports it on a
		list rather than raising it. The two must not drift."""
		structure = _structure(piecework_activity=ACTIVITY)
		with self.assertRaises(ToolError) as caught:
			wage_defaults.resolve_piece_rate(structure, [])
		self.assertEqual(str(caught.exception), wage_defaults.no_rate_message(structure, []))


# ── Claim 2: choosing the row in force ─────────────────────────────────


class ChoosingTheRowInForce(unittest.TestCase):
	def test_an_inactive_row_is_never_read_whatever_its_dates_say(self):
		self.assertFalse(wage_defaults.covers(_rate(is_active=0), "2026-06-01"))

	def test_a_string_zero_is_off(self):
		"""Frappe hands a Check back as text often enough that `bool("0")` is a
		real bug rather than a hypothetical one."""
		self.assertFalse(wage_defaults.covers(_rate(is_active="0"), "2026-06-01"))

	def test_no_date_means_whatever_is_active(self):
		self.assertTrue(wage_defaults.covers(_rate(), None))

	def test_a_closed_window_excludes_a_date_after_it(self):
		row = _rate(effective_from="2026-01-01", effective_to="2026-05-31")
		self.assertTrue(wage_defaults.covers(row, "2026-05-31"))
		self.assertFalse(wage_defaults.covers(row, "2026-06-01"))

	def test_a_date_before_the_start_is_not_covered(self):
		self.assertFalse(wage_defaults.covers(_rate(effective_from="2026-06-01"), "2026-05-31"))

	def test_the_later_effective_from_wins(self):
		"""Which is what makes a raise ONE insert: add a row from 1 June, leave
		the old one open-ended, and June onwards pays the new rate."""
		old = _rate(name="PWR-2026-0001", rate_per_unit=1.25, effective_from="2026-01-01")
		new = _rate(name="PWR-2026-0002", rate_per_unit=1.40, effective_from="2026-06-01")
		self.assertEqual(wage_defaults.select_effective([old, new], "2026-07-01")["name"], new["name"])
		self.assertEqual(wage_defaults.select_effective([old, new], "2026-05-01")["name"], old["name"])

	def test_a_datetime_shaped_value_still_compares(self):
		"""Every date reaching this module has come off a Frappe row as either a
		`date` or an ISO string, and ISO-8601 sorts correctly as text."""
		row = _rate(effective_from="2026-06-01 00:00:00")
		self.assertTrue(wage_defaults.covers(row, "2026-06-02"))
		self.assertFalse(wage_defaults.covers(row, "2026-05-31"))

	def test_nothing_in_force_is_none_rather_than_a_guess(self):
		self.assertIsNone(wage_defaults.select_effective([_rate(is_active=0)], "2026-06-01"))
		self.assertIsNone(wage_defaults.select_effective([], "2026-06-01"))

	def test_activities_available_reports_what_a_caller_may_name(self):
		rows = [_rate(), _rate(name="PWR-2", activity="Thinning"), _rate(name="PWR-3", is_active=0)]
		self.assertEqual(wage_defaults.activities_available(rows), [ACTIVITY, "thinning"])

	def test_a_position_default_is_matched_case_insensitively_by_title(self):
		rows = [{"name": "PWD-1", "designation": "Picker", "hourly_rate": 18.5, "is_active": 1}]
		self.assertEqual(wage_defaults.position_hourly_rate(rows, "picker")["name"], "PWD-1")
		self.assertIsNone(wage_defaults.position_hourly_rate(rows, "Irrigator"))
		self.assertIsNone(wage_defaults.position_hourly_rate(rows, ""))


# ── Claim 3: validation ────────────────────────────────────────────────


class Validation(unittest.TestCase):
	def test_a_valid_rate_has_nothing_wrong_with_it(self):
		self.assertEqual(wage_defaults.validate_piecework_rate(_rate()), [])

	def test_a_rate_needs_a_company_an_activity_and_a_start(self):
		errors = wage_defaults.validate_piecework_rate(
			{"company": "", "activity": "  ", "rate_per_unit": 1.0, "effective_from": None}
		)
		self.assertEqual(len(errors), 3, errors)
		self.assertTrue(any("company" in e for e in errors))
		self.assertTrue(any("activity" in e for e in errors))
		self.assertTrue(any("effective_from" in e for e in errors))

	def test_a_negative_rate_is_refused(self):
		errors = wage_defaults.validate_piecework_rate(_rate(rate_per_unit=-1))
		self.assertTrue(any("negative" in e for e in errors), errors)

	def test_a_rate_that_is_not_a_number_is_refused(self):
		errors = wage_defaults.validate_piecework_rate(_rate(rate_per_unit="soon"))
		self.assertTrue(any("must be a number" in e for e in errors), errors)

	def test_a_window_that_ends_before_it_starts_is_refused(self):
		errors = wage_defaults.validate_piecework_rate(
			_rate(effective_from="2026-06-01", effective_to="2026-05-01")
		)
		self.assertTrue(any("before" in e for e in errors), errors)

	def test_a_wage_default_needs_a_designation(self):
		errors = wage_defaults.validate_position_wage_default(
			{"company": MAIN, "designation": "", "hourly_rate": 18.5, "effective_from": "2026-01-01"}
		)
		self.assertTrue(any("designation" in e for e in errors), errors)

	def test_a_valid_wage_default_has_nothing_wrong_with_it(self):
		self.assertEqual(
			wage_defaults.validate_position_wage_default(
				{
					"company": MAIN,
					"designation": "Picker",
					"hourly_rate": 18.5,
					"effective_from": "2026-01-01",
				}
			),
			[],
		)


# ── Claim 4: the piecework rate tools ──────────────────────────────────


class WageTableTestCase(V12TestCase):
	"""A site with the eight wage-table tools switched on."""

	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **WAGE_TOOLS_ON)

	def create_rate(self, **overrides):
		args = {
			"company": MAIN,
			"activity": ACTIVITY,
			"rate_per_unit": 1.25,
			"effective_from": "2026-01-01",
		}
		args.update(overrides)
		return self.tool_data("create_piecework_rate", args)

	def seed_designations(self, *titles):
		register_doctype("Designation", [{"fieldname": "designation_name", "fieldtype": "Data"}])
		STORE.seed(
			"Designation", [{"name": title, "designation_name": title} for title in titles or ("Picker",)]
		)

	def create_default(self, **overrides):
		args = {
			"company": MAIN,
			"designation": "Picker",
			"hourly_rate": 18.5,
			"effective_from": "2026-01-01",
		}
		args.update(overrides)
		return self.tool_data("create_position_wage_default", args)


class PieceworkRateTools(WageTableTestCase):
	def test_a_rate_is_stored_normalised_and_as_entered(self):
		data = self.create_rate(activity="Bucket Segmentation")
		self.assertEqual(data["activity"], ACTIVITY)
		self.assertEqual(data["activity_as_entered"], "Bucket Segmentation")
		self.assertTrue(data["is_active"])

	def test_the_register_separates_history_from_what_is_in_force(self):
		"""Five rows for one activity is the normal state of a table that can
		still explain what May paid."""
		self.create_rate(rate_per_unit=1.25, effective_from="2026-01-01")
		raised = self.create_rate(rate_per_unit=1.40, effective_from="2026-06-01")
		data = self.tool_data("list_piecework_rates", {"company": MAIN, "on_date": "2026-07-01"})
		self.assertEqual(data["count"], 2)
		self.assertEqual([row["name"] for row in data["in_force"]], [raised["name"]])

	def test_the_register_answers_for_a_past_period_too(self):
		self.create_rate(rate_per_unit=1.25, effective_from="2026-01-01")
		self.create_rate(rate_per_unit=1.40, effective_from="2026-06-01")
		data = self.tool_data("list_piecework_rates", {"company": MAIN, "on_date": "2026-03-01"})
		self.assertEqual([row["rate_per_unit"] for row in data["in_force"]], [1.25])

	def test_a_raise_names_what_it_superseded(self):
		first = self.create_rate(effective_from="2026-01-01")
		second = self.create_rate(rate_per_unit=1.40, effective_from="2026-06-01")
		self.assertEqual(second["supersedes"], [first["name"]])
		self.assertIn(first["name"], second["note"])

	def test_a_second_active_row_starting_the_same_day_is_refused(self):
		"""Two answers to one question is not a raise, and resolving it on a
		docname would be a rate nobody can predict."""
		self.create_rate(effective_from="2026-06-01")
		error = self.tool_error(
			"create_piecework_rate",
			{
				"company": MAIN,
				"activity": "Bucket Segmentation",
				"rate_per_unit": 1.40,
				"effective_from": "2026-06-01",
			},
		)
		self.assertIn("same day", error)

	def test_the_same_day_is_free_again_for_another_activity(self):
		self.create_rate(effective_from="2026-06-01")
		other = self.create_rate(activity="thinning", rate_per_unit=0.9, effective_from="2026-06-01")
		self.assertEqual(other["activity"], "thinning")

	def test_another_company_is_a_different_table(self):
		self.create_rate(company=MAIN)
		self.create_rate(company=OTHER, rate_per_unit=1.10)
		data = self.tool_data("list_piecework_rates", {"company": OTHER})
		self.assertEqual([row["rate_per_unit"] for row in data["rates"]], [1.10])

	def test_get_says_whether_this_is_the_row_payroll_would_read(self):
		old = self.create_rate(effective_from="2026-01-01")
		new = self.create_rate(rate_per_unit=1.40, effective_from="2026-06-01")
		stale = self.tool_data("get_piecework_rate", {"name": old["name"], "on_date": "2026-07-01"})
		self.assertFalse(stale["in_force"])
		self.assertEqual(stale["in_force_instead"], new["name"])
		live = self.tool_data("get_piecework_rate", {"name": new["name"], "on_date": "2026-07-01"})
		self.assertTrue(live["in_force"])
		self.assertIsNone(live["in_force_instead"])

	def test_an_unknown_rate_is_refused_by_name_with_the_register_named(self):
		error = self.tool_error("get_piecework_rate", {"name": "PWR-9999-9999"})
		self.assertIn("PWR-9999-9999", error)
		self.assertIn("list_piecework_rates", error)

	def test_editing_a_live_rate_warns_that_a_rerun_will_now_differ(self):
		created = self.create_rate(effective_from="2026-01-01")
		data = self.tool_data(
			"update_piecework_rate", {"name": created["name"], "rate_per_unit": 1.40}
		)
		self.assertEqual(data["changed"], ["rate_per_unit"])
		self.assertIn("WARNING", data["note"])
		self.assertIn("create_piecework_rate", data["note"])

	def test_company_and_activity_cannot_be_moved(self):
		created = self.create_rate()
		for locked in ("company", "activity"):
			error = self.tool_error(
				"update_piecework_rate", {"name": created["name"], locked: "something else"}
			)
			self.assertIn(locked, error)
			self.assertIn("Nothing was changed", error)

	def test_retiring_a_rate_keeps_it_readable_and_takes_it_out_of_force(self):
		"""There is no delete. A rate that paid a period is the record of what
		that period paid."""
		created = self.create_rate()
		data = self.tool_data(
			"update_piecework_rate", {"name": created["name"], "is_active": False}
		)
		self.assertFalse(data["is_active"])
		listed = self.tool_data("list_piecework_rates", {"company": MAIN})
		self.assertEqual(listed["count"], 1)
		self.assertEqual(listed["in_force"], [])
		self.assertTrue(self.tool_data("get_piecework_rate", {"name": created["name"]}))

	def test_there_is_no_delete_tool(self):
		from erpnext_mcp import registry

		self.assertNotIn("delete_piecework_rate", registry.TOOLS)
		self.assertNotIn("delete_position_wage_default", registry.TOOLS)

	def test_an_edit_that_changes_nothing_says_so_and_writes_nothing(self):
		created = self.create_rate()
		data = self.tool_data(
			"update_piecework_rate", {"name": created["name"], "rate_per_unit": 1.25}
		)
		self.assertEqual(data["changed"], [])

	def test_a_negative_rate_never_reaches_the_database(self):
		error = self.tool_error(
			"create_piecework_rate", {"company": MAIN, "activity": ACTIVITY, "rate_per_unit": -1}
		)
		self.assertIn("negative", error)
		self.assertEqual(self.tool_data("list_piecework_rates", {"company": MAIN})["count"], 0)


# ── Claim 5: the position wage default tools ───────────────────────────


class PositionWageDefaultTools(WageTableTestCase):
	def setUp(self):
		super().setUp()
		self.seed_designations("Picker", "Irrigator")

	def test_a_default_is_created_and_read_back(self):
		created = self.create_default()
		got = self.tool_data("get_position_wage_default", {"name": created["name"]})
		self.assertEqual(got["designation"], "Picker")
		self.assertEqual(got["hourly_rate"], 18.5)
		self.assertTrue(got["in_force"])

	def test_a_job_title_this_site_does_not_have_is_refused_by_name(self):
		"""Keyed by the site's own Designation register so it can be matched
		against Employee.designation — two spellings of 'Irrigator' would be two
		rates nobody could reconcile."""
		error = self.tool_error(
			"create_position_wage_default",
			{"company": MAIN, "designation": "Tractor Whisperer", "hourly_rate": 22},
		)
		self.assertIn("Tractor Whisperer", error)
		self.assertIn("Designation", error)
		self.assertIn("Nothing was written", error)

	def test_the_register_reports_one_row_in_force_per_job_title(self):
		self.create_default(designation="Picker", hourly_rate=18.5, effective_from="2026-01-01")
		raised = self.create_default(designation="Picker", hourly_rate=19.25, effective_from="2026-06-01")
		self.create_default(designation="Irrigator", hourly_rate=21.0, effective_from="2026-01-01")
		data = self.tool_data("list_position_wage_defaults", {"company": MAIN, "on_date": "2026-07-01"})
		self.assertEqual(data["count"], 3)
		self.assertEqual(len(data["in_force"]), 2)
		self.assertIn(raised["name"], [row["name"] for row in data["in_force"]])

	def test_a_second_active_row_starting_the_same_day_is_refused(self):
		self.create_default(effective_from="2026-06-01")
		error = self.tool_error(
			"create_position_wage_default",
			{
				"company": MAIN,
				"designation": "Picker",
				"hourly_rate": 19.25,
				"effective_from": "2026-06-01",
			},
		)
		self.assertIn("same day", error)

	def test_company_and_designation_cannot_be_moved(self):
		created = self.create_default()
		for locked in ("company", "designation"):
			error = self.tool_error(
				"update_position_wage_default", {"name": created["name"], locked: "Irrigator"}
			)
			self.assertIn(locked, error)

	def test_an_edit_says_that_it_reaches_nothing_it_already_seeded(self):
		created = self.create_default()
		data = self.tool_data(
			"update_position_wage_default", {"name": created["name"], "hourly_rate": 19.25}
		)
		self.assertEqual(data["changed"], ["hourly_rate"])
		self.assertIn("UNCHANGED", data["note"])

	def test_retiring_a_default_keeps_the_row(self):
		created = self.create_default()
		self.tool_data("update_position_wage_default", {"name": created["name"], "is_active": False})
		listed = self.tool_data("list_position_wage_defaults", {"company": MAIN})
		self.assertEqual(listed["count"], 1)
		self.assertEqual(listed["in_force"], [])


# ── Claim 6: what payroll does with the tables ─────────────────────────


PAYROLL_READS_ON = {
	f"allow_{name}": 1
	for name in (
		"create_salary_structure",
		"get_salary_structure",
		"list_salary_structures",
		"preview_payroll",
		"preview_payroll_for_period",
	)
}


class PayrollReadsTheTable(PayrollTestCase):
	PIECE_WORKER = "HR-EMP-00002"

	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **WAGE_TOOLS_ON, **PAYROLL_READS_ON)

	def create_rate(self, **overrides):
		args = {
			"company": MAIN,
			"activity": ACTIVITY,
			"rate_per_unit": 1.25,
			"effective_from": "2026-01-01",
		}
		args.update(overrides)
		return self.tool_data("create_piecework_rate", args)

	def create_structure(self, **overrides):
		args = {
			"employee": self.PIECE_WORKER,
			"company": MAIN,
			"pay_type": "Piece Rate",
			"base_rate": 0,
			"effective_from": "2026-06-01",
		}
		args.update(overrides)
		return self.tool_data("create_salary_structure", args)

	def test_a_piece_rate_structure_may_be_created_with_no_rate_of_its_own(self):
		rate = self.create_rate()
		created = self.create_structure(piecework_activity="Bucket Segmentation")
		self.assertEqual(created["base_rate"], 0.0)
		self.assertEqual(created["piecework_activity"], ACTIVITY)
		self.assertEqual(created["inherits_piecework_rate"]["piecework_rate"], rate["name"])
		self.assertEqual(created["inherits_piecework_rate"]["rate_per_unit"], 1.25)

	def test_a_structure_that_would_fail_on_payday_fails_at_creation(self):
		"""In front of the person creating it, rather than in a run three weeks
		later."""
		error = self.tool_error(
			"create_salary_structure",
			{
				"employee": self.PIECE_WORKER,
				"company": MAIN,
				"pay_type": "Piece Rate",
				"base_rate": 0,
				"effective_from": "2026-06-01",
			},
		)
		self.assertIn("no active Piecework Rate", error)

	def test_zero_is_still_refused_on_a_structure_that_is_not_piece_rate(self):
		error = self.tool_error(
			"create_salary_structure",
			{"employee": "HR-EMP-00001", "company": MAIN, "pay_type": "Hourly", "base_rate": 0},
		)
		self.assertIn("positive", error)
		self.assertIn("Piece Rate", error)

	def test_an_activity_is_meaningless_on_a_structure_that_is_not_piece_rate(self):
		error = self.tool_error(
			"create_salary_structure",
			{
				"employee": "HR-EMP-00001",
				"company": MAIN,
				"pay_type": "Hourly",
				"base_rate": 20,
				"piecework_activity": ACTIVITY,
			},
		)
		self.assertIn("piecework_activity", error)
		self.assertIn("Nothing was created", error)

	def test_reading_the_structure_says_what_the_worker_is_actually_paid(self):
		"""A read that showed the zero and stopped would be a read that says a
		picker earns nothing."""
		rate = self.create_rate()
		self.create_structure()
		got = self.tool_data("get_salary_structure", {"employee": self.PIECE_WORKER})
		self.assertEqual(got["effective_rate"], 1.25)
		self.assertEqual(got["rate_source"], wage_defaults.SOURCE_COMPANY)
		self.assertEqual(got["piecework_rate"], rate["name"])

	def test_a_structure_with_its_own_rate_reports_itself_as_the_source(self):
		self.create_rate()
		self.create_structure(base_rate=1.75)
		got = self.tool_data("get_salary_structure", {"employee": self.PIECE_WORKER})
		self.assertEqual(got["effective_rate"], 1.75)
		self.assertEqual(got["rate_source"], wage_defaults.SOURCE_STRUCTURE)

	def test_reading_a_structure_whose_rate_has_gone_reports_the_gap(self):
		rate = self.create_rate()
		self.create_structure()
		self.tool_data("update_piecework_rate", {"name": rate["name"], "is_active": False})
		got = self.tool_data("get_salary_structure", {"employee": self.PIECE_WORKER})
		self.assertIsNone(got["effective_rate"])
		self.assertIn("no active Piecework Rate", got["rate_note"])

	def test_the_list_flags_the_inheritors_rather_than_resolving_them(self):
		self.create_rate()
		created = self.create_structure()
		data = self.tool_data("list_salary_structures", {"company": MAIN})
		self.assertEqual(data["inheriting_company_piecework_rate"], [created["name"]])
		self.assertIn("list_piecework_rates", data["note"])

	def test_a_single_employee_preview_raises_a_missing_rate(self):
		"""There is nobody else to hold up, so the refusal is the answer."""
		rate = self.create_rate()
		self.create_structure()
		self.tool_data("update_piecework_rate", {"name": rate["name"], "is_active": False})
		error = self.tool_error(
			"preview_payroll",
			{
				"employee": self.PIECE_WORKER,
				"company": MAIN,
				"pay_period_start": "2026-06-01",
				"pay_period_end": "2026-06-15",
			},
		)
		self.assertIn("no active Piecework Rate", error)

	def test_a_company_run_reports_the_fallback_working(self):
		rate = self.create_rate()
		self.create_structure()
		data = self.tool_data(
			"preview_payroll_for_period",
			{"company": MAIN, "pay_period_start": "2026-06-01", "pay_period_end": "2026-06-15"},
		)
		self.assertEqual(
			[row["piecework_rate"] for row in data["piece_rates_from_company"]], [rate["name"]]
		)
		self.assertEqual(data["employees_missing_piece_rates"], [])

	def test_a_company_run_reports_a_missing_rate_and_does_not_abort(self):
		"""One worker's missing rate does not hold up everybody else's pay —
		the posture run_payroll_for_period has taken towards a missing salary
		structure since v0.35.0."""
		rate = self.create_rate()
		self.create_structure()
		self.tool_data("create_salary_structure", {
			"employee": "HR-EMP-00001",
			"company": MAIN,
			"pay_type": "Hourly",
			"base_rate": 20.0,
			"effective_from": "2026-06-01",
		})
		self.tool_data("update_piecework_rate", {"name": rate["name"], "is_active": False})
		data = self.tool_data(
			"preview_payroll_for_period",
			{"company": MAIN, "pay_period_start": "2026-06-01", "pay_period_end": "2026-06-15"},
		)
		missing = data["employees_missing_piece_rates"]
		self.assertEqual([row["employee"] for row in missing], [self.PIECE_WORKER])
		self.assertIn("no active Piecework Rate", missing[0]["reason"])
		self.assertEqual(data["piece_rates_from_company"], [])

	def test_the_period_is_priced_at_the_rate_in_force_when_it_closed(self):
		"""`on_date` is the pay period's END, so a period straddling a rate
		change pays what was in force when it closed."""
		self.create_rate(rate_per_unit=1.25, effective_from="2026-01-01")
		raised = self.create_rate(rate_per_unit=1.40, effective_from="2026-06-10")
		self.create_structure()
		data = self.tool_data(
			"preview_payroll_for_period",
			{"company": MAIN, "pay_period_start": "2026-06-01", "pay_period_end": "2026-06-15"},
		)
		self.assertEqual(
			[(row["piecework_rate"], row["rate"]) for row in data["piece_rates_from_company"]],
			[(raised["name"], 1.40)],
		)

	def test_an_hourly_structure_is_seeded_from_the_position_wage_default(self):
		"""Read ONCE, at creation, and copied onto the structure: an hourly wage
		is what somebody was hired at."""
		register_doctype("Designation", [{"fieldname": "designation_name", "fieldtype": "Data"}])
		STORE.seed("Designation", [{"name": "Picker", "designation_name": "Picker"}])
		STORE.get_raw("Employee", "HR-EMP-00001")["designation"] = "Picker"
		default = self.tool_data(
			"create_position_wage_default",
			{
				"company": MAIN,
				"designation": "Picker",
				"hourly_rate": 18.5,
				"effective_from": "2026-01-01",
			},
		)
		created = self.tool_data(
			"create_salary_structure",
			{
				"employee": "HR-EMP-00001",
				"company": MAIN,
				"pay_type": "Hourly",
				"effective_from": "2026-06-01",
			},
		)
		self.assertEqual(created["base_rate"], 18.5)
		self.assertEqual(
			created["seeded_from_defaults"]["base_rate"]["position_wage_default"], default["name"]
		)

	def test_editing_the_default_does_not_reach_back_through_the_structure(self):
		"""THE ASYMMETRY WITH PIECEWORK RATE IS THE DESIGN. A table that could
		restate somebody's agreed hourly rate for a period already worked would
		be a table that rewrites what a wage claim asks about."""
		register_doctype("Designation", [{"fieldname": "designation_name", "fieldtype": "Data"}])
		STORE.seed("Designation", [{"name": "Picker", "designation_name": "Picker"}])
		STORE.get_raw("Employee", "HR-EMP-00001")["designation"] = "Picker"
		default = self.tool_data(
			"create_position_wage_default",
			{
				"company": MAIN,
				"designation": "Picker",
				"hourly_rate": 18.5,
				"effective_from": "2026-01-01",
			},
		)
		self.tool_data(
			"create_salary_structure",
			{
				"employee": "HR-EMP-00001",
				"company": MAIN,
				"pay_type": "Hourly",
				"effective_from": "2026-06-01",
			},
		)
		self.tool_data("update_position_wage_default", {"name": default["name"], "hourly_rate": 25.0})
		got = self.tool_data("get_salary_structure", {"employee": "HR-EMP-00001"})
		self.assertEqual(got["effective_rate"], 18.5)

	def test_a_caller_who_names_a_rate_is_not_overruled_by_the_default(self):
		register_doctype("Designation", [{"fieldname": "designation_name", "fieldtype": "Data"}])
		STORE.seed("Designation", [{"name": "Picker", "designation_name": "Picker"}])
		STORE.get_raw("Employee", "HR-EMP-00001")["designation"] = "Picker"
		self.tool_data(
			"create_position_wage_default",
			{"company": MAIN, "designation": "Picker", "hourly_rate": 18.5},
		)
		created = self.tool_data(
			"create_salary_structure",
			{
				"employee": "HR-EMP-00001",
				"company": MAIN,
				"pay_type": "Hourly",
				"base_rate": 21.0,
				"effective_from": "2026-06-01",
			},
		)
		self.assertEqual(created["base_rate"], 21.0)
		self.assertIsNone(created["seeded_from_defaults"])


if __name__ == "__main__":
	unittest.main()
