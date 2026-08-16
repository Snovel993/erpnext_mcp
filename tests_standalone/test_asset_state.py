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
        error = self.tool_error("log_asset_state_change", {
            "asset_name": "MC-Valve-05",
            "action": "explode",
        })
        self.assertIn("not a valid action", error)

    def test_invalid_transition_is_refused(self):
        self.an_asset()
        error = self.tool_error("log_asset_state_change", {
            "asset_name": "MC-Valve-05",
            "action": "close_valve",
        })
        self.assertIn("Cannot", error)
        self.assertIn("closed", error)

    def test_winterize_requires_closed(self):
        self.an_asset()
        self.do_action("MC-Valve-05", "open_valve")
        error = self.tool_error("log_asset_state_change", {
            "asset_name": "MC-Valve-05",
            "action": "winterize",
        })
        self.assertIn("Cannot", error)

    def test_reopen_from_winterized(self):
        self.an_asset()
        self.do_action("MC-Valve-05", "winterize")
        data = self.do_action("MC-Valve-05", "reopen")
        self.assertEqual(data["from_state"], "winterized")
        self.assertEqual(data["to_state"], "closed")

    def test_notes_are_stored(self):
        self.an_asset()
        data = self.do_action("MC-Valve-05", "open_valve", notes="Turned on for irrigation")
        history = self.tool_data("list_asset_state_history", {"asset_name": "MC-Valve-05"})
        event = history["events"][0]
        self.assertEqual(event["notes"], "Turned on for irrigation")

    def test_gps_is_stored(self):
        self.an_asset()
        data = self.do_action("MC-Valve-05", "open_valve", gps_lat=45.5152, gps_lon=-122.6784)
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
        error = self.tool_error("log_asset_state_change", {
            "asset_name": "MC-Valve-05",
            "action": "winterize",
        })
        self.assertIn("Cannot", error)

    def test_cannot_open_winterized_valve_without_reopen(self):
        """Must reopen (to closed) before opening."""
        self.an_asset()
        self.do_action("MC-Valve-05", "winterize")
        error = self.tool_error("log_asset_state_change", {
            "asset_name": "MC-Valve-05",
            "action": "open_valve",
        })
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
        data = self.tool_data("list_asset_state_history", {
            "asset_name": "MC-Valve-05",
            "limit": 2,
        })
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
        error = self.tool_error("log_asset_state_change", {
            "asset_name": "MC-Valve-05",
            "action": "open_valve",
        })
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
            "Housing Unit", "Irrigation Zone", "Irrigation Valve",
            "Sprayer", "Tractor", "Block", "Water Source",
            "Storage", "Cold Storage", "General",
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
        self.assertEqual(
            [entry["reason"] for entry in data["cascade_skipped"]], ["retired"]
        )

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
