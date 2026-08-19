# SPDX-License-Identifier: MIT
"""Tests for v0.25.0 — Asset state-change actions."""

from .fixtures import MAIN, V12TestCase
from .harness import STORE

ALL_ON = {
	"allow_list_assets": 1,
	"allow_get_asset_detail": 1,
	"allow_get_asset_history": 1,
	"allow_scan_asset": 1,
	"allow_register_asset": 1,
	"allow_update_registered_asset": 1,
	"allow_retire_asset": 1,
	"allow_get_available_actions": 1,
	"allow_log_asset_state_change": 1,
	"allow_list_asset_state_history": 1,
}


class StateTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def an_asset(self, name="MC-Valve-05", asset_type="Irrigation Valve", company=MAIN, **kw):
		payload = {"name": name, "asset_type": asset_type, "company": company, **kw}
		return self.tool_data("register_asset", payload)

	def do_action(self, asset_name, action, **kw):
		payload = {"asset_name": asset_name, "action": action, **kw}
		return self.tool_data("log_asset_state_change", payload)


# ── get_available_actions ─────────────────────────────────────────────────────
class GetAvailableActions(StateTestCase):
	def test_valve_defaults_to_closed(self):
		self.an_asset()
		data = self.tool_data("get_available_actions", {"asset_name": "MC-Valve-05"})
		self.assertEqual(data["current_state"], "closed")
		actions = [a["action"] for a in data["available_actions"]]
		self.assertIn("open_valve", actions)
		self.assertIn("winterize", actions)
		self.assertNotIn("close_valve", actions)

	def test_sprayer_defaults_to_empty(self):
		self.an_asset(name="MC-Sprayer-01", asset_type="Sprayer")
		data = self.tool_data("get_available_actions", {"asset_name": "MC-Sprayer-01"})
		self.assertEqual(data["current_state"], "empty")
		actions = [a["action"] for a in data["available_actions"]]
		self.assertIn("fill_tank", actions)
		self.assertIn("clean_tank", actions)
		self.assertNotIn("start_spray", actions)

	def test_tractor_defaults_to_in_service(self):
		self.an_asset(name="MC-Tractor-01", asset_type="Tractor")
		data = self.tool_data("get_available_actions", {"asset_name": "MC-Tractor-01"})
		self.assertEqual(data["current_state"], "in_service")
		actions = [a["action"] for a in data["available_actions"]]
		self.assertIn("take_out_of_service", actions)
		self.assertIn("start_maintenance", actions)
		self.assertNotIn("put_in_service", actions)

	def test_housing_unit_defaults_to_vacant(self):
		self.an_asset(name="MC-Cabin-01", asset_type="Housing Unit")
		data = self.tool_data("get_available_actions", {"asset_name": "MC-Cabin-01"})
		self.assertEqual(data["current_state"], "vacant")
		actions = [a["action"] for a in data["available_actions"]]
		self.assertIn("mark_occupied", actions)
		self.assertIn("winterize", actions)

	def test_block_defaults_to_active(self):
		self.an_asset(name="MC-Block-A", asset_type="Block")
		data = self.tool_data("get_available_actions", {"asset_name": "MC-Block-A"})
		self.assertEqual(data["current_state"], "active")
		actions = [a["action"] for a in data["available_actions"]]
		self.assertIn("set_dormant", actions)
		self.assertIn("set_fallow", actions)

	def test_water_source_defaults_to_active(self):
		self.an_asset(name="MC-Well-01", asset_type="Water Source")
		data = self.tool_data("get_available_actions", {"asset_name": "MC-Well-01"})
		self.assertEqual(data["current_state"], "active")
		actions = [a["action"] for a in data["available_actions"]]
		self.assertIn("deactivate", actions)
		self.assertIn("log_treatment", actions)
		self.assertIn("mark_contaminated", actions)

	def test_irrigation_zone_defaults_to_active(self):
		self.an_asset(name="MC-Zone-01", asset_type="Irrigation Zone")
		data = self.tool_data("get_available_actions", {"asset_name": "MC-Zone-01"})
		self.assertEqual(data["current_state"], "active")

	def test_storage_defaults_to_closed(self):
		self.an_asset(name="MC-Store-01", asset_type="Storage")
		data = self.tool_data("get_available_actions", {"asset_name": "MC-Store-01"})
		self.assertEqual(data["current_state"], "closed")
		actions = [a["action"] for a in data["available_actions"]]
		self.assertIn("open_for_season", actions)

	def test_cold_storage_defaults_to_closed(self):
		self.an_asset(name="MC-Cold-01", asset_type="Cold Storage")
		data = self.tool_data("get_available_actions", {"asset_name": "MC-Cold-01"})
		self.assertEqual(data["current_state"], "closed")

	def test_general_defaults_to_active(self):
		self.an_asset(name="MC-Gen-01", asset_type="General")
		data = self.tool_data("get_available_actions", {"asset_name": "MC-Gen-01"})
		self.assertEqual(data["current_state"], "active")

	def test_all_states_is_populated(self):
		self.an_asset()
		data = self.tool_data("get_available_actions", {"asset_name": "MC-Valve-05"})
		self.assertIn("open", data["all_states"])
		self.assertIn("closed", data["all_states"])
		self.assertIn("winterized", data["all_states"])

	def test_actions_reflect_current_state(self):
		self.an_asset()
		self.do_action("MC-Valve-05", "open_valve")
		data = self.tool_data("get_available_actions", {"asset_name": "MC-Valve-05"})
		self.assertEqual(data["current_state"], "open")
		actions = [a["action"] for a in data["available_actions"]]
		self.assertIn("close_valve", actions)
		self.assertNotIn("open_valve", actions)


# ── log_asset_state_change ────────────────────────────────────────────────────
class LogStateChange(StateTestCase):
	def test_basic_transition(self):
		self.an_asset()
		data = self.do_action("MC-Valve-05", "open_valve")
		self.assertEqual(data["from_state"], "closed")
		self.assertEqual(data["to_state"], "open")
		self.assertEqual(data["action"], "open_valve")
		self.assertIsNotNone(data["log_name"])

	def test_state_is_updated_on_asset(self):
		self.an_asset()
		self.do_action("MC-Valve-05", "open_valve")
		detail = self.tool_data("get_asset_detail", {"asset_name": "MC-Valve-05"})
		self.assertEqual(detail["current_state"]["state"], "open")

	def test_sequential_transitions(self):
		self.an_asset()
		self.do_action("MC-Valve-05", "open_valve")
		data = self.do_action("MC-Valve-05", "close_valve")
		self.assertEqual(data["from_state"], "open")
		self.assertEqual(data["to_state"], "closed")

	def test_invalid_action_is_refused(self):
		self.an_asset()
		error = self.tool_error(
			"log_asset_state_change",
			{
				"asset_name": "MC-Valve-05",
				"action": "explode",
			},
		)
		self.assertIn("not a valid action", error)

	def test_invalid_transition_is_refused(self):
		self.an_asset()
		error = self.tool_error(
			"log_asset_state_change",
			{
				"asset_name": "MC-Valve-05",
				"action": "close_valve",
			},
		)
		self.assertIn("Cannot", error)
		self.assertIn("closed", error)

	def test_winterize_requires_closed(self):
		self.an_asset()
		self.do_action("MC-Valve-05", "open_valve")
		error = self.tool_error(
			"log_asset_state_change",
			{
				"asset_name": "MC-Valve-05",
				"action": "winterize",
			},
		)
		self.assertIn("Cannot", error)

	def test_reopen_from_winterized(self):
		self.an_asset()
		self.do_action("MC-Valve-05", "winterize")
		data = self.do_action("MC-Valve-05", "reopen")
		self.assertEqual(data["from_state"], "winterized")
		self.assertEqual(data["to_state"], "closed")

	def test_notes_are_stored(self):
		self.an_asset()
		self.do_action("MC-Valve-05", "open_valve", notes="Turned on for irrigation")
		history = self.tool_data("list_asset_state_history", {"asset_name": "MC-Valve-05"})
		event = history["events"][0]
		self.assertEqual(event["notes"], "Turned on for irrigation")

	def test_gps_is_stored(self):
		self.an_asset()
		self.do_action("MC-Valve-05", "open_valve", gps_lat=45.5152, gps_lon=-122.6784)
		history = self.tool_data("list_asset_state_history", {"asset_name": "MC-Valve-05"})
		event = history["events"][0]
		self.assertAlmostEqual(event["gps_latitude"], 45.5152, places=4)
		self.assertAlmostEqual(event["gps_longitude"], -122.6784, places=4)

	def test_sprayer_full_cycle(self):
		self.an_asset(name="MC-Sprayer-01", asset_type="Sprayer")
		self.do_action("MC-Sprayer-01", "fill_tank")
		self.do_action("MC-Sprayer-01", "start_spray")
		data = self.do_action("MC-Sprayer-01", "end_spray")
		self.assertEqual(data["to_state"], "empty")

	def test_tractor_maintenance_cycle(self):
		self.an_asset(name="MC-Tractor-01", asset_type="Tractor")
		self.do_action("MC-Tractor-01", "start_maintenance")
		data = self.do_action("MC-Tractor-01", "end_maintenance")
		self.assertEqual(data["to_state"], "in_service")

	def test_housing_occupancy_cycle(self):
		self.an_asset(name="MC-Cabin-01", asset_type="Housing Unit")
		self.do_action("MC-Cabin-01", "mark_occupied")
		data = self.do_action("MC-Cabin-01", "mark_vacant")
		self.assertEqual(data["to_state"], "vacant")

	def test_water_source_treatment(self):
		self.an_asset(name="MC-Well-01", asset_type="Water Source")
		data = self.do_action("MC-Well-01", "log_treatment")
		self.assertEqual(data["to_state"], "treated")

	def test_water_source_contamination_and_clearance(self):
		self.an_asset(name="MC-Well-01", asset_type="Water Source")
		self.do_action("MC-Well-01", "mark_contaminated")
		self.do_action("MC-Well-01", "log_treatment")
		detail = self.tool_data("get_asset_detail", {"asset_name": "MC-Well-01"})
		self.assertEqual(detail["current_state"]["state"], "treated")

	def test_block_lifecycle(self):
		self.an_asset(name="MC-Block-A", asset_type="Block")
		self.do_action("MC-Block-A", "set_dormant")
		data = self.do_action("MC-Block-A", "activate")
		self.assertEqual(data["to_state"], "active")

	def test_storage_season_cycle(self):
		self.an_asset(name="MC-Store-01", asset_type="Storage")
		self.do_action("MC-Store-01", "open_for_season")
		self.do_action("MC-Store-01", "log_temperature")
		data = self.do_action("MC-Store-01", "close_for_season")
		self.assertEqual(data["to_state"], "off_season")

	def test_irrigation_zone_winterize(self):
		self.an_asset(name="MC-Zone-01", asset_type="Irrigation Zone")
		self.do_action("MC-Zone-01", "winterize")
		data = self.do_action("MC-Zone-01", "activate")
		self.assertEqual(data["to_state"], "active")

	def test_general_repair_cycle(self):
		self.an_asset(name="MC-Gen-01", asset_type="General")
		self.do_action("MC-Gen-01", "flag_repair")
		data = self.do_action("MC-Gen-01", "clear_repair")
		self.assertEqual(data["to_state"], "active")

	def test_cannot_winterize_open_valve(self):
		"""Winterizing requires closed first — can't skip straight from open."""
		self.an_asset()
		self.do_action("MC-Valve-05", "open_valve")
		error = self.tool_error(
			"log_asset_state_change",
			{
				"asset_name": "MC-Valve-05",
				"action": "winterize",
			},
		)
		self.assertIn("Cannot", error)

	def test_cannot_open_winterized_valve_without_reopen(self):
		"""Must reopen (to closed) before opening."""
		self.an_asset()
		self.do_action("MC-Valve-05", "winterize")
		error = self.tool_error(
			"log_asset_state_change",
			{
				"asset_name": "MC-Valve-05",
				"action": "open_valve",
			},
		)
		self.assertIn("Cannot", error)

	def test_is_audited(self):
		self.an_asset()
		self.do_action("MC-Valve-05", "open_valve")
		self.assertAudited("log_asset_state_change", "Success")


# ── list_asset_state_history ──────────────────────────────────────────────────
class ListStateHistory(StateTestCase):
	def test_empty_history(self):
		self.an_asset()
		data = self.tool_data("list_asset_state_history", {"asset_name": "MC-Valve-05"})
		self.assertEqual(data["event_count"], 0)
		self.assertEqual(data["events"], [])

	def test_history_after_actions(self):
		self.an_asset()
		self.do_action("MC-Valve-05", "open_valve")
		self.do_action("MC-Valve-05", "close_valve")
		data = self.tool_data("list_asset_state_history", {"asset_name": "MC-Valve-05"})
		self.assertEqual(data["event_count"], 2)
		self.assertEqual(data["events"][0]["action"], "close_valve")
		self.assertEqual(data["events"][1]["action"], "open_valve")

	def test_history_respects_limit(self):
		self.an_asset()
		self.do_action("MC-Valve-05", "open_valve")
		self.do_action("MC-Valve-05", "close_valve")
		self.do_action("MC-Valve-05", "open_valve")
		data = self.tool_data(
			"list_asset_state_history",
			{
				"asset_name": "MC-Valve-05",
				"limit": 2,
			},
		)
		self.assertEqual(data["event_count"], 2)

	def test_state_changes_appear_in_asset_history(self):
		self.an_asset()
		self.do_action("MC-Valve-05", "open_valve")
		data = self.tool_data("get_asset_history", {"asset_name": "MC-Valve-05"})
		doctypes = [e["doctype"] for e in data["events"]]
		self.assertIn("Asset State Log", doctypes)


# ── kill switches ─────────────────────────────────────────────────────────────
class StateKillSwitches(StateTestCase):
	def test_get_available_actions_on_by_default(self):
		self.configure(enabled=1, allow_register_asset=1)
		self.an_asset()
		self.configure(enabled=1)
		data = self.tool_data("get_available_actions", {"asset_name": "MC-Valve-05"})
		self.assertIn("available_actions", data)

	def test_log_state_change_off_by_default(self):
		self.configure(enabled=1, allow_register_asset=1)
		self.an_asset()
		self.configure(enabled=1)
		error = self.tool_error(
			"log_asset_state_change",
			{
				"asset_name": "MC-Valve-05",
				"action": "open_valve",
			},
		)
		self.assertIn("switched off", error.lower())

	def test_list_state_history_on_by_default(self):
		self.configure(enabled=1, allow_register_asset=1)
		self.an_asset()
		self.configure(enabled=1)
		data = self.tool_data("list_asset_state_history", {"asset_name": "MC-Valve-05"})
		self.assertEqual(data["event_count"], 0)


# ── all ten asset types have state definitions ────────────────────────────────
class AllTypesCovered(StateTestCase):
	def test_every_type_has_a_default_state_and_at_least_one_action(self):
		types = [
			"Housing Unit",
			"Irrigation Zone",
			"Irrigation Valve",
			"Sprayer",
			"Tractor",
			"Block",
			"Water Source",
			"Storage",
			"Cold Storage",
			"General",
		]
		for i, asset_type in enumerate(types):
			name = f"TEST-{i:02d}"
			self.an_asset(name=name, asset_type=asset_type)
			data = self.tool_data("get_available_actions", {"asset_name": name})
			self.assertTrue(
				data["current_state"],
				f"{asset_type} has no default state",
			)
			self.assertTrue(
				len(data["available_actions"]) > 0,
				f"{asset_type} has no available actions from default state",
			)


# ── the cascade: a main valve shuts everything below it ───────────────────────
class ClosingAMainValveCascades(StateTestCase):
	"""v0.76.0. Shutting an upstream valve shuts the line below it.

	The register has to agree with the pipe. A worker sent to a line break shuts
	the main at the turnout; every valve below it is dry from that moment, and a
	register still reporting them `open` is the reading the next person acts on.
	"""

	def a_line(self):
		"""A turnout with two laterals under it, and a valve under one of those.

		Three levels rather than two on purpose: the cascade has to reach the
		whole subtree, and a walk that only read direct children would pass a
		two-level fixture.
		"""
		self.an_asset(name="MC-Main-01")
		self.an_asset(name="MC-Lateral-A", location="MC-Main-01")
		self.an_asset(name="MC-Lateral-B", location="MC-Main-01")
		self.an_asset(name="MC-Drop-A1", location="MC-Lateral-A")
		for name in ("MC-Main-01", "MC-Lateral-A", "MC-Lateral-B", "MC-Drop-A1"):
			self.do_action(name, "open_valve")

	def state_of(self, name):
		return self.tool_data("get_available_actions", {"asset_name": name})["current_state"]

	def test_closing_the_main_closes_every_valve_below_it(self):
		self.a_line()
		data = self.do_action("MC-Main-01", "close_valve")

		self.assertEqual(data["to_state"], "closed")
		self.assertEqual(data["cascaded_count"], 3)
		self.assertEqual(
			sorted(entry["asset_name"] for entry in data["cascaded"]),
			["MC-Drop-A1", "MC-Lateral-A", "MC-Lateral-B"],
		)
		for name in ("MC-Lateral-A", "MC-Lateral-B", "MC-Drop-A1"):
			self.assertEqual(self.state_of(name), "closed", f"{name} was left open")

	def test_the_cascade_reaches_a_grandchild(self):
		"""The valve two levels down is the one a direct-children walk would miss."""
		self.a_line()
		data = self.do_action("MC-Main-01", "close_valve")
		reached = [entry["asset_name"] for entry in data["cascaded"]]
		self.assertIn("MC-Drop-A1", reached)

	def test_opening_the_main_does_not_cascade(self):
		"""The asymmetry is physical: closing upstream stops the water, opening
		it only makes water available to valves that are shut on their own."""
		self.a_line()
		self.do_action("MC-Main-01", "close_valve")
		data = self.do_action("MC-Main-01", "open_valve")

		self.assertEqual(data["cascaded_count"], 0)
		for name in ("MC-Lateral-A", "MC-Lateral-B", "MC-Drop-A1"):
			self.assertEqual(self.state_of(name), "closed")

	def test_each_cascaded_valve_gets_its_own_log_entry(self):
		self.a_line()
		self.do_action("MC-Main-01", "close_valve")
		history = self.tool_data("list_asset_state_history", {"asset_name": "MC-Lateral-B"})
		closes = [event for event in history["events"] if event["action"] == "close_valve"]

		self.assertEqual(len(closes), 1)
		self.assertEqual(closes[0]["from_state"], "open")
		self.assertEqual(closes[0]["to_state"], "closed")
		self.assertTrue(closes[0]["performed_at"], "a cascaded close carries no timestamp")

	def test_a_cascaded_close_names_the_valve_that_caused_it(self):
		self.a_line()
		self.do_action("MC-Main-01", "close_valve")
		history = self.tool_data("list_asset_state_history", {"asset_name": "MC-Lateral-B"})
		close = next(e for e in history["events"] if e["action"] == "close_valve")

		self.assertEqual(close["cascaded_from"], "MC-Main-01")
		self.assertTrue(close["cascaded"])
		self.assertIn("MC-Main-01", close["notes"])

	def test_a_hand_closed_valve_is_not_marked_as_cascaded(self):
		self.a_line()
		self.do_action("MC-Lateral-B", "close_valve")
		history = self.tool_data("list_asset_state_history", {"asset_name": "MC-Lateral-B"})
		close = next(e for e in history["events"] if e["action"] == "close_valve")

		self.assertIsNone(close["cascaded_from"])
		self.assertFalse(close["cascaded"])

	def test_a_valve_already_closed_is_skipped_with_a_reason(self):
		self.a_line()
		self.do_action("MC-Lateral-B", "close_valve")
		data = self.do_action("MC-Main-01", "close_valve")

		self.assertEqual(data["cascaded_count"], 2)
		skipped = {entry["asset_name"]: entry["reason"] for entry in data["cascade_skipped"]}
		self.assertIn("MC-Lateral-B", skipped)
		self.assertIn("closed", skipped["MC-Lateral-B"])

	def test_a_child_that_is_not_a_valve_is_skipped_rather_than_forced(self):
		"""A pump or a sensor hung under a valve has no close_valve action, and
		inventing one for it would be a state machine per convenience."""
		self.an_asset(name="MC-Main-02")
		self.an_asset(name="MC-Pump-01", asset_type="Tractor", location="MC-Main-02")
		self.do_action("MC-Main-02", "open_valve")

		data = self.do_action("MC-Main-02", "close_valve")
		self.assertEqual(data["cascaded_count"], 0)
		skipped = {entry["asset_name"]: entry["reason"] for entry in data["cascade_skipped"]}
		self.assertIn("MC-Pump-01", skipped)
		self.assertIn("close_valve", skipped["MC-Pump-01"])

	def test_a_retired_valve_is_skipped(self):
		self.an_asset(name="MC-Main-03")
		self.an_asset(name="MC-Old-01", location="MC-Main-03")
		self.do_action("MC-Main-03", "open_valve")
		self.do_action("MC-Old-01", "open_valve")
		self.tool_data("retire_asset", {"asset_name": "MC-Old-01"})

		data = self.do_action("MC-Main-03", "close_valve")
		self.assertEqual(data["cascaded_count"], 0)
		self.assertEqual([entry["reason"] for entry in data["cascade_skipped"]], ["retired"])

	def test_a_valve_with_nothing_under_it_cascades_to_nothing(self):
		self.an_asset(name="MC-Lonely-01")
		self.do_action("MC-Lonely-01", "open_valve")
		data = self.do_action("MC-Lonely-01", "close_valve")

		self.assertEqual(data["cascaded_count"], 0)
		self.assertEqual(data["cascade_skipped"], [])
		self.assertFalse(data["cascade_truncated"])

	def test_a_parent_loop_does_not_hang_the_cascade(self):
		"""`Asset Register.validate` refuses an asset that is its own parent and
		nothing refuses A → B → A. A walk that trusted the tree would spin."""
		self.an_asset(name="MC-Loop-A")
		self.an_asset(name="MC-Loop-B", location="MC-Loop-A")
		STORE.tables["Asset Register"]["MC-Loop-A"]["location"] = "MC-Loop-B"
		self.do_action("MC-Loop-A", "open_valve")
		self.do_action("MC-Loop-B", "open_valve")

		data = self.do_action("MC-Loop-A", "close_valve")
		self.assertEqual([entry["asset_name"] for entry in data["cascaded"]], ["MC-Loop-B"])

	def test_a_refused_action_changes_nothing_downstream(self):
		"""The transition is validated before anything is written, cascade
		included: a close that cannot happen must not shut the line."""
		self.a_line()
		self.do_action("MC-Main-01", "close_valve")
		error = self.tool_error(
			"log_asset_state_change", {"asset_name": "MC-Main-01", "action": "close_valve"}
		)
		self.assertIn("Cannot", error)

	def test_only_the_scanned_valve_carries_the_gps_fix(self):
		"""A fix taken at the turnout is not where the lateral is, and a cascaded
		row must not claim somebody was standing at it."""
		self.a_line()
		self.do_action("MC-Main-01", "close_valve", gps_lat=46.6, gps_lon=-120.5)

		main = self.tool_data("list_asset_state_history", {"asset_name": "MC-Main-01"})
		lateral = self.tool_data("list_asset_state_history", {"asset_name": "MC-Lateral-A"})
		self.assertEqual(main["events"][0]["gps_latitude"], 46.6)
		self.assertIsNone(lateral["events"][0]["gps_latitude"])


# ── the menu a handset draws ──────────────────────────────────────────────────
class TheActionMenuIsPerEquipmentType(StateTestCase):
	"""v0.77.0. What comes up after a scan depends on what was scanned.

	`state_actions` is the state machine — the transitions legal right now — and
	that is strictly smaller than the menu a screen has to lay out. A pre-trip
	inspection is not a state change. Neither is logging engine hours.
	"""

	def menu(self, asset_name):
		return self.tool_data("get_available_actions", {"asset_name": asset_name})["action_menu"]

	def by_action(self, asset_name):
		return {entry["action"]: entry for entry in self.menu(asset_name)}

	def test_a_valve_offers_on_and_off_in_a_workers_words(self):
		self.an_asset()
		entries = self.by_action("MC-Valve-05")
		self.assertEqual(entries["open_valve"]["label"], "Turn On")
		self.assertEqual(entries["close_valve"]["label"], "Turn Off")

	def test_a_closed_valve_can_be_opened_and_not_closed(self):
		self.an_asset()
		entries = self.by_action("MC-Valve-05")
		self.assertTrue(entries["open_valve"]["available"])
		self.assertFalse(entries["close_valve"]["available"])
		self.assertIn("closed", entries["close_valve"]["unavailable_reason"])

	def test_a_sprayer_offers_mixing_spraying_and_an_inspection(self):
		self.an_asset(name="MC-Sprayer-01", asset_type="Sprayer")
		entries = self.by_action("MC-Sprayer-01")
		self.assertEqual(entries["fill_tank"]["label"], "Mix / Load Tank")
		self.assertTrue(entries["fill_tank"]["available"])
		self.assertEqual(entries["pre_use_inspection"]["method"], "start_inspection")
		self.assertTrue(entries["pre_use_inspection"]["implemented"])

	def test_a_tractor_offers_checkout_and_a_pre_trip_inspection(self):
		self.an_asset(name="MC-Tractor-01", asset_type="Tractor")
		entries = self.by_action("MC-Tractor-01")
		self.assertEqual(entries["check_out"]["label"], "Check Out")
		self.assertTrue(entries["check_out"]["available"])
		self.assertEqual(entries["pre_trip_inspection"]["method"], "start_inspection")

	def test_checking_a_tractor_out_and_back_in_works_end_to_end(self):
		self.an_asset(name="MC-Tractor-02", asset_type="Tractor")
		out = self.do_action("MC-Tractor-02", "check_out")
		self.assertEqual(out["to_state"], "checked_out")

		entries = self.by_action("MC-Tractor-02")
		self.assertTrue(entries["check_in"]["available"])
		self.assertFalse(entries["check_out"]["available"])

		back = self.do_action("MC-Tractor-02", "check_in")
		self.assertEqual(back["to_state"], "in_service")

	def test_a_tractor_that_breaks_while_checked_out_can_go_to_maintenance(self):
		"""Which is where a breakdown happens. `put_in_service` deliberately
		does not reach from `checked_out`: a machine coming back from the field
		is checked IN, by the person who has it."""
		self.an_asset(name="MC-Tractor-03", asset_type="Tractor")
		self.do_action("MC-Tractor-03", "check_out")
		entries = self.by_action("MC-Tractor-03")
		self.assertTrue(entries["start_maintenance"]["available"])
		self.assertFalse(entries["put_in_service"]["available"])

	def test_a_vehicle_asks_the_same_questions_as_a_tractor(self):
		self.an_asset(name="MC-Truck-01", asset_type="Vehicle")
		entries = self.by_action("MC-Truck-01")
		self.assertTrue(entries["check_out"]["available"])
		self.assertIn("pre_trip_inspection", entries)

	def test_an_implement_attaches_and_detaches(self):
		self.an_asset(name="MC-Disc-01", asset_type="Implement")
		entries = self.by_action("MC-Disc-01")
		self.assertEqual(entries["attach"]["label"], "Attach to Tractor")
		self.assertTrue(entries["attach"]["available"])
		self.assertFalse(entries["detach"]["available"])

		self.do_action("MC-Disc-01", "attach")
		self.assertTrue(self.by_action("MC-Disc-01")["detach"]["available"])

	def test_which_tractor_an_implement_is_on_is_the_register_tree(self):
		"""Not a state. `parent_asset` already points one asset at another, and
		duplicating the link inside a state blob gives one fact two homes."""
		self.an_asset(name="MC-Disc-02", asset_type="Implement")
		entry = self.by_action("MC-Disc-02")["set_tractor"]
		self.assertEqual(entry["method"], "update_registered_asset")
		self.assertTrue(entry["implemented"])
		self.assertIn("parent_asset", entry["note"])

	def test_an_unbuilt_action_is_published_and_says_it_is_unbuilt(self):
		"""Publishing only the finished actions gives iOS no way to lay out a
		screen it will need next month; publishing them undifferentiated gives a
		worker a button that fails after they have walked to the machine.

		`set_rates` is the example rather than `log_hours` because v0.78.0 BUILT
		the latter — see the test below. This assertion is about the shape an
		unbuilt row has, and it wants a row that is genuinely still unbuilt.
		"""
		self.an_asset(name="MC-Sprayer-04", asset_type="Sprayer")
		entry = self.by_action("MC-Sprayer-04")["set_rates"]
		self.assertFalse(entry["implemented"])
		self.assertFalse(entry["available"])
		self.assertIsNone(entry["method"])
		self.assertIn("NOT BUILT", entry["note"])

	def test_logging_engine_hours_stopped_being_unbuilt(self):
		"""v0.78.0. The note that used to sit on this row said building it meant
		a Float column plus a log row per entry, because the reading is only
		useful as a SERIES. That is exactly what shipped — and the action is not
		a call of its own, because the moment somebody reads an hour meter is
		the moment they are checking the machine out or bringing it back."""
		self.an_asset(name="MC-Tractor-04", asset_type="Tractor")
		entry = self.by_action("MC-Tractor-04")["log_hours"]
		self.assertTrue(entry["implemented"])
		self.assertTrue(entry["available"])
		self.assertEqual(entry["method"], "log_asset_state_change")
		self.assertIn("engine_hours", entry["note"])

	def test_unbuilt_and_unavailable_are_different_reasons(self):
		"""A screen greys out one and badges or hides the other, and it cannot
		tell them apart from a single flag."""
		self.an_asset(name="MC-Sprayer-05", asset_type="Sprayer")
		self.assertIn("not implemented", self.by_action("MC-Sprayer-05")["set_rates"]["unavailable_reason"])

		self.an_asset(name="MC-Tractor-05", asset_type="Tractor")
		self.assertIn("not a legal move", self.by_action("MC-Tractor-05")["check_in"]["unavailable_reason"])

	def test_every_row_carries_the_keys_a_client_switches_on(self):
		self.an_asset(name="MC-Sprayer-02", asset_type="Sprayer")
		for entry in self.menu("MC-Sprayer-02"):
			with self.subTest(action=entry["action"]):
				for key in ("action", "label", "kind", "method", "implemented", "available"):
					self.assertIn(key, entry)

	def test_reporting_a_problem_is_offered_on_everything(self):
		for name, asset_type in (("MC-V-1", "Irrigation Valve"), ("MC-T-1", "Tractor"), ("MC-B-1", "Block")):
			with self.subTest(asset_type=asset_type):
				self.an_asset(name=name, asset_type=asset_type)
				self.assertIn("report_issue", self.by_action(name))

	def test_a_type_with_no_hand_written_menu_still_lists_its_transitions(self):
		"""Block, Water Source and General have states and no equipment
		workflow. The table exists for types whose menu is MORE than their state
		machine, not to decide which types have one."""
		self.an_asset(name="MC-Block-Z", asset_type="Block")
		entries = self.by_action("MC-Block-Z")
		self.assertIn("set_dormant", entries)
		self.assertTrue(entries["set_dormant"]["available"])

	def test_every_state_change_row_names_the_endpoint_that_performs_it(self):
		self.an_asset()
		for entry in self.menu("MC-Valve-05"):
			if entry["kind"] == "state_change":
				self.assertEqual(entry["method"], "log_asset_state_change")

	def test_an_available_state_row_carries_the_transition_it_would_make(self):
		self.an_asset()
		entry = self.by_action("MC-Valve-05")["open_valve"]
		self.assertEqual(entry["from_state"], "closed")
		self.assertEqual(entry["to_state"], "open")

	def test_a_scan_returns_the_same_menu_the_actions_call_does(self):
		"""One card, one code path on the handset."""
		self.an_asset(name="MC-Sprayer-03", asset_type="Sprayer")
		scanned = self.tool_data("scan_asset", {"asset_name": "MC-Sprayer-03"})
		self.assertEqual(scanned["action_menu"], self.menu("MC-Sprayer-03"))
		self.assertEqual(scanned["state"], "empty")


# ── which six o'clock ─────────────────────────────────────────────────────────
class TheValveLogSaysWhichClock(StateTestCase):
	"""v0.77.0. "The valve went off at 18:12" is only a sentence with a zone."""

	def setUp(self):
		super().setUp()
		STORE.singles["System Settings"] = {"time_zone": "America/Los_Angeles"}

	def test_a_toggle_answers_with_an_offset_and_the_stored_value(self):
		self.an_asset()
		data = self.do_action("MC-Valve-05", "open_valve")
		self.assertEqual(data["timezone"], "America/Los_Angeles")
		self.assertEqual(data["stored_timezone"], "America/Los_Angeles")
		self.assertTrue(data["performed_at_local"].endswith("-07:00"))
		self.assertNotIn("T", data["performed_at"])

	def test_the_history_carries_one_per_event(self):
		self.an_asset()
		self.do_action("MC-Valve-05", "open_valve")
		self.do_action("MC-Valve-05", "close_valve")
		history = self.tool_data("list_asset_state_history", {"asset_name": "MC-Valve-05"})
		self.assertEqual(len(history["events"]), 2)
		for event in history["events"]:
			self.assertTrue(event["performed_at_local"].endswith(("-07:00", "-08:00")))

	def test_a_requested_zone_is_honoured(self):
		self.an_asset()
		data = self.do_action("MC-Valve-05", "open_valve", timezone="America/Denver")
		self.assertEqual(data["timezone"], "America/Denver")
		self.assertEqual(data["stored_timezone"], "America/Los_Angeles")
		self.assertTrue(data["performed_at_local"].endswith("-06:00"))

	def test_an_unset_site_zone_says_it_fell_back_rather_than_claiming_utc(self):
		STORE.singles.pop("System Settings", None)
		self.an_asset()
		data = self.do_action("MC-Valve-05", "open_valve")
		self.assertEqual(data["timezone"], "UTC")
		self.assertIn("not set", data["timezone_source"])
