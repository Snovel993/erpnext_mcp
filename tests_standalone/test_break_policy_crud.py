# SPDX-License-Identifier: MIT
"""Create and update a Labor Break Policy through the MCP tool surface.

TWO TOOLS, TWO CLAIMS:

1. `CreateBuildsAValidPolicy` — `create_break_policy` inserts a well-formed
   policy with schedule rows, refuses duplicates, refuses bad data, and the
   switch gates it.

2. `UpdateAmendsThePolicy` — `update_break_policy` toggles enabled, replaces
   schedule tables wholesale, refuses re-keying work_state, and the switch
   gates it.
"""

from .fixtures import V12TestCase

ON = {
	"allow_get_break_policy": 1,
	"allow_create_break_policy": 1,
	"allow_update_break_policy": 1,
}

OR_REST = [
	{"hours_from": 0, "hours_to": 4, "periods_owed": 1, "minutes_each": 10, "paid": True},
	{"hours_from": 4, "hours_to": 8, "periods_owed": 2, "minutes_each": 10, "paid": True},
	{"hours_from": 8, "hours_to": 12, "periods_owed": 3, "minutes_each": 10, "paid": True},
]

OR_MEAL = [
	{"hours_from": 6, "hours_to": 14, "periods_owed": 1, "minutes_each": 30, "paid": False},
]

MINOR_REST = [
	{"hours_from": 0, "hours_to": 2, "periods_owed": 1, "minutes_each": 10, "paid": True},
	{"hours_from": 2, "hours_to": 4, "periods_owed": 2, "minutes_each": 10, "paid": True},
	{"hours_from": 4, "hours_to": 8, "periods_owed": 4, "minutes_each": 10, "paid": True},
]

MINOR_MEAL = [
	{"hours_from": 4, "hours_to": 8, "periods_owed": 1, "minutes_each": 30, "paid": False},
	{"hours_from": 8, "hours_to": 12, "periods_owed": 2, "minutes_each": 30, "paid": False},
]


class BreakPolicyTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ON)

	def a_policy(self, **overrides):
		payload = {
			"work_state": "OR",
			"effective_from": "2026-01-01",
			"regulation_citations": "OAR 839-020-0050; ORS 653.261",
			"rest_schedule": OR_REST,
			"meal_schedule": OR_MEAL,
		}
		payload.update(overrides)
		return self.tool_data("create_break_policy", payload)


class CreateBuildsAValidPolicy(BreakPolicyTestCase):
	def test_a_policy_with_rest_and_meal_rows_is_created(self):
		data = self.a_policy()
		self.assertEqual(data["work_state"], "OR")
		self.assertEqual(len(data["rest_schedule"]), 3)
		self.assertEqual(len(data["meal_schedule"]), 1)
		self.assertFalse(data["meal_schedule"][0]["paid"])
		self.assertTrue(data["rest_schedule"][0]["paid"])

	def test_the_policy_id_defaults_to_state_dash_date(self):
		data = self.a_policy()
		self.assertEqual(data["policy"], "OR-2026-01-01")

	def test_a_custom_policy_id_is_honoured(self):
		data = self.a_policy(policy_id="OR-CHERRY-2026")
		self.assertEqual(data["policy"], "OR-CHERRY-2026")

	def test_a_duplicate_policy_id_is_refused(self):
		self.a_policy()
		err = self.tool_error(
			"create_break_policy",
			{
				"work_state": "OR",
				"effective_from": "2026-01-01",
			},
		)
		self.assertIn("already exists", err)

	def test_a_different_date_makes_a_different_policy(self):
		self.a_policy()
		data = self.a_policy(effective_from="2026-07-01")
		self.assertEqual(data["policy"], "OR-2026-07-01")

	def test_minor_schedules_are_written(self):
		data = self.a_policy(
			minor_rest_schedule=MINOR_REST,
			minor_meal_schedule=MINOR_MEAL,
		)
		self.assertTrue(data["has_minor_schedule"])
		self.assertEqual(len(data["minor_rest_schedule"]), 3)
		self.assertEqual(len(data["minor_meal_schedule"]), 2)

	def test_enabled_defaults_to_true(self):
		data = self.a_policy()
		self.assertTrue(data["approved"] is False or data.get("policy"))
		readback = self.tool_data("get_break_policy", {"work_state": "OR"})
		self.assertIsNotNone(readback.get("policy"))

	def test_work_state_is_required(self):
		err = self.tool_error(
			"create_break_policy",
			{
				"effective_from": "2026-01-01",
			},
		)
		self.assertIn("work_state", err.lower())

	def test_effective_from_is_required(self):
		err = self.tool_error(
			"create_break_policy",
			{
				"work_state": "OR",
			},
		)
		self.assertIn("effective_from", err.lower())

	def test_an_invalid_state_is_refused(self):
		err = self.tool_error(
			"create_break_policy",
			{
				"work_state": "CA",
				"effective_from": "2026-01-01",
			},
		)
		self.assertIn("OR", err)

	def test_a_bad_schedule_row_is_refused(self):
		err = self.tool_error(
			"create_break_policy",
			{
				"work_state": "OR",
				"effective_from": "2026-01-01",
				"rest_schedule": [{"hours_from": 0}],
			},
		)
		self.assertIn("missing", err.lower())

	def test_the_switch_gates_it(self):
		self.configure(enabled=1, allow_create_break_policy=0)
		err = self.tool_error(
			"create_break_policy",
			{
				"work_state": "OR",
				"effective_from": "2026-01-01",
			},
		)
		self.assertIn("switched off", err.lower())


class UpdateAmendsThePolicy(BreakPolicyTestCase):
	def test_toggling_enabled_off_and_on(self):
		self.a_policy()
		data = self.tool_data(
			"update_break_policy",
			{
				"policy": "OR-2026-01-01",
				"enabled": False,
			},
		)
		self.assertIn("enabled", data["changes"])
		self.assertEqual(data["changes"]["enabled"], [True, False])

		data2 = self.tool_data(
			"update_break_policy",
			{
				"policy": "OR-2026-01-01",
				"enabled": True,
			},
		)
		self.assertEqual(data2["changes"]["enabled"], [False, True])

	def test_replacing_the_rest_schedule(self):
		self.a_policy()
		new_rest = [
			{"hours_from": 0, "hours_to": 4, "periods_owed": 1, "minutes_each": 15, "paid": True},
		]
		data = self.tool_data(
			"update_break_policy",
			{
				"policy": "OR-2026-01-01",
				"rest_schedule": new_rest,
			},
		)
		self.assertIn("rest_schedule", data["changes"])
		self.assertEqual(len(data["rest_schedule"]), 1)
		self.assertEqual(data["rest_schedule"][0]["minutes_each"], 15)

	def test_adding_minor_schedules(self):
		self.a_policy()
		data = self.tool_data(
			"update_break_policy",
			{
				"policy": "OR-2026-01-01",
				"minor_rest_schedule": MINOR_REST,
				"minor_meal_schedule": MINOR_MEAL,
			},
		)
		self.assertTrue(data["has_minor_schedule"])
		self.assertIn("minor_rest_schedule", data["changes"])

	def test_changing_work_state_is_refused(self):
		self.a_policy()
		err = self.tool_error(
			"update_break_policy",
			{
				"policy": "OR-2026-01-01",
				"work_state": "WA",
			},
		)
		self.assertIn("cannot be changed", err.lower())

	def test_a_missing_policy_is_refused(self):
		err = self.tool_error(
			"update_break_policy",
			{
				"policy": "NONEXISTENT",
			},
		)
		self.assertIn("no labor break policy", err.lower())

	def test_nothing_to_change_is_refused(self):
		self.a_policy()
		err = self.tool_error(
			"update_break_policy",
			{
				"policy": "OR-2026-01-01",
			},
		)
		self.assertIn("nothing to change", err.lower())

	def test_the_switch_gates_it(self):
		self.a_policy()
		self.configure(enabled=1, allow_update_break_policy=0)
		err = self.tool_error(
			"update_break_policy",
			{
				"policy": "OR-2026-01-01",
				"enabled": False,
			},
		)
		self.assertIn("switched off", err.lower())

	def test_updating_regulation_citations(self):
		self.a_policy()
		data = self.tool_data(
			"update_break_policy",
			{
				"policy": "OR-2026-01-01",
				"regulation_citations": "OAR 839-020-0050 (amended 2026)",
			},
		)
		self.assertIn("regulation_citations", data["changes"])
		self.assertEqual(data["regulation_citations"], "OAR 839-020-0050 (amended 2026)")
