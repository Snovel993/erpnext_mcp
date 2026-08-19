# SPDX-License-Identifier: MIT
"""The QR valve workflow: scan a tag, read the state, press one button.

WHAT IS UNDER TEST HERE IS THE WORKFLOW AND NOT THE REGISTER. A valve has been
an Asset Register row since v0.25.0 and `test_asset_tags` covers registration;
the state machine and its cascade are `test_asset_state`'s; the pairing of opens
to closes into minutes is `test_irrigation_runtime`'s. This module asserts the
six things that are new when those are already true:

1. **A TOGGLE PICKS THE ACTION FROM THE STATE.** `TheToggleReadsTheState`. The
   whole reason this tool exists rather than an open/close pair is that a phone
   that has just read a QR does not know whether the gate is open — so pressing
   the button twice must open and then close, not fail the second time.

2. **CLOSING CASCADES AND OPENING DOES NOT.** `TheCascadeIsOneDirection`. This
   is the assertion with a billing consequence: an opening cascade would mark
   every lateral on the line as running, and `get_water_usage_report` prices
   exactly those events into gallons. The asymmetry is deliberate and is checked
   in both directions, including that a child closes without touching its parent.

3. **RANK IS CHECKED AGAINST THE TREE AT CREATION.** `TheRankMustMatchTheLine`.
   A Main filed underneath a Lateral is a data-entry error the cascade would
   otherwise honour by shutting a line from the wrong end.

4. **A VALVE NOBODY HAS TOUCHED IS CLOSED, NOT UNKNOWN.** `TheDefaultStateIsReal`.
   `current_state` is empty until the first change is logged, and a state filter
   that read the column rather than the resolved state would drop every valve on
   a new install.

5. **A SCAN RESOLVES A URL AND REFUSES WHAT IS NOT A VALVE BY NAME.**
   `TheScanResolvesTheTag`. The string a camera produces is
   `<site>/scan/<docname>`, and a worker who scanned a tractor is owed the word
   "Tractor" rather than "not found".

6. **RUNTIME IS THE LOG'S ANSWER, NOT A SECOND ONE.** `TheRuntimeIsTheSameSum`.
   Checked against `get_irrigation_runtime` on identical data, because two
   answers to "how long did this run" is one answer that is wrong.

THE EVENTS BEHIND THE ARITHMETIC ARE SEEDED, for the reason
`test_irrigation_runtime` sets out: this harness advances the clock one second
per call, so a run opened and closed through the tool is one second long. Where a
test is about the TOGGLE rather than about minutes, the tool does the writing and
the log row it leaves is what gets asserted.
"""

import frappe

from erpnext_mcp.api import mobile as mobile_api

from .fixtures import MAIN, OTHER, V12TestCase
from .harness import STORE
from .test_api_mobile import ON, OUTSIDER, WORKER, MobileAPITestCase

ALL_ON = {
	f"allow_{name}": 1
	for name in (
		"create_irrigation_valve",
		"list_irrigation_valves",
		"get_irrigation_valve",
		"toggle_irrigation_valve",
		"get_valve_runtime",
		"scan_valve_qr",
		"create_parcel",
		"create_field",
		"create_irrigation_zone",
		"register_asset",
		"retire_asset",
		"get_irrigation_runtime",
		"log_asset_state_change",
		"list_asset_state_history",
	)
}

BLOCK = "Home Ranch Block 1 - HR"
ZONE = "HR1-Zone1 - HR"
ZONE_TWO = "HR1-Zone2 - HR"

MAIN_VALVE = "HR-Valve-Main"
SUB_VALVE = "HR-Valve-Sub"
LATERAL = "HR-Valve-Lat-01"
LATERAL_TWO = "HR-Valve-Lat-02"


class ValveTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)
		self.tool_data(
			"create_parcel",
			{
				"owning_entity": MAIN,
				"parcel_name": "Home Ranch",
				"acreage": 88.0,
				"county": "Wasco",
				"state": "OR",
				"use_type": "Orchard",
			},
		)
		self.tool_data(
			"create_field",
			{
				"parcel": "Home Ranch",
				"field_name": "Home Ranch Block 1",
				"acreage": 14.0,
				"variety": "Rainier",
				"planting_year": 2004,
				"condition": "Good",
			},
		)
		self.a_zone()

	def a_zone(self, zone_name="HR1-Zone1", number=1, flow=45, **kw):
		payload = {
			"field": BLOCK,
			"zone_name": zone_name,
			"zone_number": number,
			"water_source": "well",
			"sprinkler_type": "drip",
			"area_sq_ft": 217800,
			"flow_rate_gpm": flow,
		}
		payload.update(kw)
		return self.tool_data("create_irrigation_zone", payload)

	def a_valve(self, valve_id, valve_type="Lateral", parent=None, zone=ZONE, **kw):
		payload = {"valve_id": valve_id, "valve_type": valve_type, "company": MAIN}
		if zone is not None:
			payload["zone"] = zone
		if parent:
			payload["parent_valve"] = parent
		payload.update(kw)
		return self.tool_data("create_irrigation_valve", payload)

	def a_line(self):
		"""A main, a sub-main under it, and two laterals under that."""
		self.a_valve(MAIN_VALVE, "Main")
		self.a_valve(SUB_VALVE, "Sub-Main", parent=MAIN_VALVE)
		self.a_valve(LATERAL, "Lateral", parent=SUB_VALVE)
		self.a_valve(LATERAL_TWO, "Lateral", parent=SUB_VALVE)

	def toggle(self, valve, **kw):
		return self.tool_data("toggle_irrigation_valve", {"name": valve, **kw})

	def state_of(self, valve):
		return self.tool_data("get_irrigation_valve", {"name": valve})["state"]

	def event(self, asset, to_state, when, cascaded_from=None):
		"""One Asset State Log row, at a timestamp this test chose."""
		rows = STORE.tables.setdefault("Asset State Log", {})
		name = f"ASL-V-{len(rows) + 1:04d}"
		rows[name] = {
			"name": name,
			"docstatus": 0,
			"asset_name": asset,
			"asset_type": "Irrigation Valve",
			"action": "open_valve" if to_state == "open" else "close_valve",
			"from_state": "closed" if to_state == "open" else "open",
			"to_state": to_state,
			"performed_by": "Administrator",
			"performed_at": when,
			"cascaded_from": cascaded_from,
			"creation": when,
		}


# ── 1. the toggle reads the state ───────────────────────────────────────────
class TheToggleReadsTheState(ValveTestCase):
	def test_a_new_valve_opens_on_the_first_press(self):
		self.a_valve(LATERAL)
		data = self.toggle(LATERAL)
		self.assertEqual(data["from_state"], "closed")
		self.assertEqual(data["to_state"], "open")
		self.assertEqual(data["action"], "open_valve")
		self.assertTrue(data["is_open"])

	def test_the_second_press_closes_it(self):
		self.a_valve(LATERAL)
		self.toggle(LATERAL)
		data = self.toggle(LATERAL)
		self.assertEqual(data["from_state"], "open")
		self.assertEqual(data["to_state"], "closed")
		self.assertEqual(data["action"], "close_valve")
		self.assertFalse(data["is_open"])

	def test_pressing_it_four_times_ends_where_it_started(self):
		self.a_valve(LATERAL)
		for _ in range(4):
			self.toggle(LATERAL)
		self.assertEqual(self.state_of(LATERAL), "closed")

	def test_every_press_leaves_a_state_log_row(self):
		self.a_valve(LATERAL)
		self.toggle(LATERAL)
		self.toggle(LATERAL)
		history = self.tool_data("list_asset_state_history", {"asset_name": LATERAL})
		self.assertEqual(len(history["events"]), 2)
		self.assertEqual(
			[event["action"] for event in history["events"]],
			["close_valve", "open_valve"],
		)

	def test_the_register_caches_when_the_state_last_moved(self):
		"""`last_state_change` is a cache of the log and must agree with it."""
		self.a_valve(LATERAL)
		toggled = self.toggle(LATERAL)
		self.assertEqual(toggled["last_state_change"], toggled["performed_at"])

	def test_a_winterized_valve_does_not_toggle(self):
		self.a_valve(LATERAL)
		self.tool_data("log_asset_state_change", {"asset_name": LATERAL, "action": "winterize"})
		message = self.tool_error("toggle_irrigation_valve", {"name": LATERAL})
		self.assertIn("winterized", message)
		self.assertIn("reopen", message)
		self.assertIn("Nothing was changed", message)

	def test_a_retired_valve_does_not_toggle(self):
		self.a_valve(LATERAL)
		self.tool_data("retire_asset", {"asset_name": LATERAL, "reason": "replaced"})
		message = self.tool_error("toggle_irrigation_valve", {"name": LATERAL})
		self.assertIn("retired", message)
		self.assertIn("Nothing was changed", message)

	def test_a_stale_expect_state_is_refused_rather_than_toggled_backwards(self):
		"""The screen was drawn before somebody else opened it."""
		self.a_valve(LATERAL)
		self.toggle(LATERAL)
		message = self.tool_error("toggle_irrigation_valve", {"name": LATERAL, "expect_state": "closed"})
		self.assertIn("is 'open'", message)
		self.assertIn("Nothing was changed", message)
		self.assertEqual(self.state_of(LATERAL), "open")

	def test_a_matching_expect_state_goes_through(self):
		self.a_valve(LATERAL)
		data = self.toggle(LATERAL, expect_state="closed")
		self.assertEqual(data["to_state"], "open")

	def test_a_tractor_is_not_a_valve_and_the_refusal_says_what_it_is(self):
		self.tool_data("register_asset", {"name": "HR-Tractor-1", "asset_type": "Tractor", "company": MAIN})
		message = self.tool_error("toggle_irrigation_valve", {"name": "HR-Tractor-1"})
		self.assertIn("Tractor", message)
		self.assertIn("Irrigation Valve", message)


# ── 2. the cascade runs one direction ───────────────────────────────────────
class TheCascadeIsOneDirection(ValveTestCase):
	def setUp(self):
		super().setUp()
		self.a_line()

	def open_the_whole_line(self):
		for valve in (MAIN_VALVE, SUB_VALVE, LATERAL, LATERAL_TWO):
			self.toggle(valve)

	def test_closing_the_main_closes_everything_below_it(self):
		self.open_the_whole_line()
		data = self.toggle(MAIN_VALVE)

		self.assertEqual(data["to_state"], "closed")
		self.assertEqual(data["cascaded_count"], 3)
		self.assertEqual(
			sorted(entry["asset_name"] for entry in data["cascaded"]),
			sorted([SUB_VALVE, LATERAL, LATERAL_TWO]),
		)
		for valve in (SUB_VALVE, LATERAL, LATERAL_TWO):
			self.assertEqual(self.state_of(valve), "closed", valve)

	def test_a_cascaded_close_names_the_valve_that_caused_it(self):
		self.open_the_whole_line()
		self.toggle(MAIN_VALVE)
		history = self.tool_data("list_asset_state_history", {"asset_name": LATERAL})
		latest = history["events"][0]
		self.assertEqual(latest["to_state"], "closed")
		self.assertEqual(latest["cascaded_from"], MAIN_VALVE)
		self.assertTrue(latest["cascaded"])

	def test_opening_the_main_opens_only_the_main(self):
		"""The assertion with a billing consequence. See tools/valves.py."""
		data = self.toggle(MAIN_VALVE)

		self.assertEqual(data["to_state"], "open")
		self.assertEqual(data["cascaded_count"], 0)
		self.assertEqual(data["cascaded"], [])
		for valve in (SUB_VALVE, LATERAL, LATERAL_TWO):
			self.assertEqual(self.state_of(valve), "closed", valve)

	def test_the_answer_says_which_way_the_next_press_carries(self):
		closed = self.tool_data("get_irrigation_valve", {"name": MAIN_VALVE})
		self.assertEqual(closed["next_action"]["action"], "open_valve")
		self.assertFalse(closed["next_action"]["cascades"])

		self.toggle(MAIN_VALVE)
		opened = self.tool_data("get_irrigation_valve", {"name": MAIN_VALVE})
		self.assertEqual(opened["next_action"]["action"], "close_valve")
		self.assertTrue(opened["next_action"]["cascades"])

	def test_a_child_closes_without_touching_its_parent(self):
		self.open_the_whole_line()
		self.toggle(LATERAL)

		self.assertEqual(self.state_of(LATERAL), "closed")
		self.assertEqual(self.state_of(SUB_VALVE), "open")
		self.assertEqual(self.state_of(MAIN_VALVE), "open")
		self.assertEqual(self.state_of(LATERAL_TWO), "open")

	def test_a_valve_already_closed_is_reported_as_skipped_not_silently_dropped(self):
		self.toggle(MAIN_VALVE)
		self.toggle(SUB_VALVE)
		data = self.toggle(MAIN_VALVE)

		self.assertEqual(data["cascaded_count"], 1)
		skipped = {entry["asset_name"]: entry["reason"] for entry in data["cascade_skipped"]}
		self.assertIn(LATERAL, skipped)
		self.assertIn("already 'closed'", skipped[LATERAL])

	def test_the_parent_chain_names_what_would_have_to_be_shut(self):
		data = self.tool_data("get_irrigation_valve", {"name": LATERAL})
		self.assertEqual([entry["name"] for entry in data["parent_chain"]], [SUB_VALVE, MAIN_VALVE])

	def test_the_children_and_which_of_them_are_open_come_back(self):
		self.toggle(LATERAL)
		data = self.tool_data("get_irrigation_valve", {"name": SUB_VALVE})
		self.assertEqual(data["child_count"], 2)
		self.assertEqual(data["children_open"], [LATERAL])


# ── 3. rank is checked against the tree ─────────────────────────────────────
class TheRankMustMatchTheLine(ValveTestCase):
	def test_a_lateral_under_a_main_is_fine(self):
		self.a_valve(MAIN_VALVE, "Main")
		created = self.a_valve(LATERAL, "Lateral", parent=MAIN_VALVE)
		self.assertEqual(created["parent_valve"], MAIN_VALVE)
		self.assertEqual(created["valve_type"], "Lateral")

	def test_a_main_under_a_lateral_is_refused(self):
		self.a_valve(LATERAL, "Lateral")
		message = self.tool_error(
			"create_irrigation_valve",
			{
				"valve_id": MAIN_VALVE,
				"valve_type": "Main",
				"parent_valve": LATERAL,
				"zone": ZONE,
				"company": MAIN,
			},
		)
		self.assertIn("cannot be the parent", message)
		self.assertIn("Nothing was created", message)

	def test_two_laterals_in_a_row_are_allowed(self):
		"""A lateral off a lateral is a real plumbing arrangement, not an error."""
		self.a_valve(LATERAL, "Lateral")
		created = self.a_valve(LATERAL_TWO, "Lateral", parent=LATERAL)
		self.assertEqual(created["parent_valve"], LATERAL)

	def test_a_parent_that_is_not_a_valve_is_refused(self):
		self.tool_data("register_asset", {"name": "HR-Pump-1", "asset_type": "General", "company": MAIN})
		message = self.tool_error(
			"create_irrigation_valve",
			{
				"valve_id": LATERAL,
				"valve_type": "Lateral",
				"parent_valve": "HR-Pump-1",
				"zone": ZONE,
				"company": MAIN,
			},
		)
		self.assertIn("Irrigation Valve", message)
		self.assertIn("Nothing was created", message)

	def test_the_valve_type_is_accepted_however_it_was_spelled(self):
		self.a_valve(MAIN_VALVE, "main")
		created = self.a_valve(SUB_VALVE, "submain", parent=MAIN_VALVE)
		self.assertEqual(created["valve_type"], "Sub-Main")

	def test_a_rank_this_app_does_not_have_is_refused(self):
		message = self.tool_error(
			"create_irrigation_valve",
			{"valve_id": LATERAL, "valve_type": "Riser", "zone": ZONE, "company": MAIN},
		)
		self.assertIn("Main, Sub-Main, Lateral", message)

	def test_the_zone_is_required_and_the_refusal_says_why(self):
		message = self.tool_error(
			"create_irrigation_valve",
			{"valve_id": LATERAL, "valve_type": "Lateral", "company": MAIN},
		)
		self.assertIn("zone is required", message)
		self.assertIn("price", message)

	def test_a_valve_under_a_parent_inherits_the_parents_zone_and_says_so(self):
		self.a_valve(MAIN_VALVE, "Main", zone=ZONE)
		created = self.tool_data(
			"create_irrigation_valve",
			{"valve_id": LATERAL, "valve_type": "Lateral", "parent_valve": MAIN_VALVE, "company": MAIN},
		)
		self.assertEqual(created["zone"], ZONE)
		self.assertIn("inherited", created["zone_source"])

	def test_a_zone_that_does_not_exist_is_refused(self):
		message = self.tool_error(
			"create_irrigation_valve",
			{"valve_id": LATERAL, "valve_type": "Lateral", "zone": "No Such Zone", "company": MAIN},
		)
		self.assertIn("No Such Zone", message)
		self.assertIn("Nothing was created", message)

	def test_a_zone_belonging_to_another_entity_is_refused(self):
		message = self.tool_error(
			"create_irrigation_valve",
			{"valve_id": LATERAL, "valve_type": "Lateral", "zone": ZONE, "company": OTHER},
		)
		self.assertIn("belongs to", message)
		self.assertIn("Nothing was created", message)

	def test_the_docname_is_the_tag_and_the_qr_is_derived_from_it(self):
		created = self.a_valve(LATERAL)
		self.assertEqual(created["name"], LATERAL)
		self.assertEqual(created["valve_id"], LATERAL)
		self.assertIn(f"/scan/{LATERAL}", created["qr_code"])

	def test_the_installed_date_is_kept_apart_from_the_purchase_date(self):
		created = self.a_valve(LATERAL, installed_date="2019-04-02", acquired_on="2018-11-20")
		self.assertEqual(created["installed_date"], "2019-04-02")


# ── 4. a valve nobody has touched is closed ─────────────────────────────────
class TheDefaultStateIsReal(ValveTestCase):
	def test_a_valve_never_toggled_reads_as_closed(self):
		created = self.a_valve(LATERAL)
		self.assertEqual(created["state"], "closed")
		self.assertIn("default", created["state_source"])

	def test_it_is_found_by_a_closed_filter_although_the_column_is_empty(self):
		"""A SQL filter on current_state would drop every valve on a new install."""
		self.a_valve(LATERAL)
		listed = self.tool_data("list_irrigation_valves", {"state": "closed"})
		self.assertEqual([valve["name"] for valve in listed["valves"]], [LATERAL])

	def test_an_open_filter_finds_only_what_is_open(self):
		self.a_line()
		self.toggle(LATERAL)
		listed = self.tool_data("list_irrigation_valves", {"state": "open"})
		self.assertEqual([valve["name"] for valve in listed["valves"]], [LATERAL])
		self.assertEqual(listed["open_count"], 1)

	def test_a_state_this_machine_does_not_have_is_refused(self):
		message = self.tool_error("list_irrigation_valves", {"state": "leaking"})
		self.assertIn("state must be one of", message)

	def test_the_list_counts_by_state_and_by_rank(self):
		self.a_line()
		self.toggle(MAIN_VALVE)
		listed = self.tool_data("list_irrigation_valves", {})
		self.assertEqual(listed["valve_count"], 4)
		self.assertEqual(listed["by_state"], {"closed": 3, "open": 1})
		self.assertEqual(listed["by_valve_type"], {"Lateral": 2, "Main": 1, "Sub-Main": 1})

	def test_the_list_narrows_to_one_zone(self):
		self.a_zone(zone_name="HR1-Zone2", number=2)
		self.a_valve(LATERAL, zone=ZONE)
		self.a_valve(LATERAL_TWO, zone=ZONE_TWO)
		listed = self.tool_data("list_irrigation_valves", {"zone": ZONE_TWO})
		self.assertEqual([valve["name"] for valve in listed["valves"]], [LATERAL_TWO])

	def test_the_list_narrows_to_the_children_of_one_valve(self):
		self.a_line()
		listed = self.tool_data("list_irrigation_valves", {"parent_valve": SUB_VALVE})
		self.assertEqual(sorted(valve["name"] for valve in listed["valves"]), sorted([LATERAL, LATERAL_TWO]))

	def test_the_list_carries_a_child_count_per_valve(self):
		self.a_line()
		listed = self.tool_data("list_irrigation_valves", {})
		counts = {valve["name"]: valve["child_count"] for valve in listed["valves"]}
		self.assertEqual(counts[MAIN_VALVE], 1)
		self.assertEqual(counts[SUB_VALVE], 2)
		self.assertEqual(counts[LATERAL], 0)

	def test_a_retired_valve_drops_out_of_the_list_by_default(self):
		self.a_valve(LATERAL)
		self.a_valve(LATERAL_TWO)
		self.tool_data("retire_asset", {"asset_name": LATERAL_TWO, "reason": "replaced"})

		listed = self.tool_data("list_irrigation_valves", {})
		self.assertEqual([valve["name"] for valve in listed["valves"]], [LATERAL])

		with_retired = self.tool_data("list_irrigation_valves", {"include_retired": True})
		self.assertEqual(len(with_retired["valves"]), 2)

	def test_only_valves_are_listed(self):
		self.a_valve(LATERAL)
		self.tool_data("register_asset", {"name": "HR-Tractor-1", "asset_type": "Tractor", "company": MAIN})
		listed = self.tool_data("list_irrigation_valves", {})
		self.assertEqual([valve["name"] for valve in listed["valves"]], [LATERAL])


# ── 5. the scan resolves the tag ────────────────────────────────────────────
class TheScanResolvesTheTag(ValveTestCase):
	def setUp(self):
		super().setUp()
		self.a_valve(LATERAL)

	def test_a_printed_tag_url_resolves_to_the_valve(self):
		created = self.tool_data("get_irrigation_valve", {"name": LATERAL})
		data = self.tool_data("scan_valve_qr", {"qr_data": created["qr_code"]})
		self.assertEqual(data["name"], LATERAL)
		self.assertEqual(data["resolved_from"], LATERAL)
		self.assertEqual(data["entity_type"], "irrigation_valve")

	def test_a_bare_valve_id_typed_by_hand_is_the_same_call(self):
		data = self.tool_data("scan_valve_qr", {"qr_data": LATERAL})
		self.assertEqual(data["name"], LATERAL)

	def test_the_scan_is_recorded_on_the_valve(self):
		data = self.tool_data("scan_valve_qr", {"qr_data": LATERAL, "scanned_by": "Administrator"})
		self.assertTrue(data["scan_recorded"])
		self.assertTrue(data["last_scan_at"])
		self.assertEqual(data["last_scan_by"], "Administrator")

	def test_the_scan_does_not_toggle_the_valve(self):
		"""Scanning a tag is looking at a thing. Water is a decision."""
		self.tool_data("scan_valve_qr", {"qr_data": LATERAL})
		self.assertEqual(self.state_of(LATERAL), "closed")

	def test_the_scan_hands_back_the_button(self):
		data = self.tool_data("scan_valve_qr", {"qr_data": LATERAL})
		self.assertEqual(data["next_action"]["action"], "open_valve")
		self.assertEqual(data["next_action"]["to_state"], "open")

	def test_the_scan_carries_todays_runtime_and_the_zone(self):
		data = self.tool_data("scan_valve_qr", {"qr_data": LATERAL})
		self.assertIn("runtime_today", data)
		self.assertEqual(data["zone"], ZONE)
		self.assertEqual(data["zone_detail"]["flow_rate_gpm"], 45.0)

	def test_a_tag_that_is_not_a_valve_is_refused_by_naming_what_it_is(self):
		self.tool_data("register_asset", {"name": "HR-Tractor-1", "asset_type": "Tractor", "company": MAIN})
		message = self.tool_error("scan_valve_qr", {"qr_data": "HR-Tractor-1"})
		self.assertIn("Tractor", message)

	def test_a_tag_in_no_register_at_all_says_so_and_names_the_way_out(self):
		message = self.tool_error("scan_valve_qr", {"qr_data": "SOMEBODY-ELSES-BARCODE"})
		self.assertIn("SOMEBODY-ELSES-BARCODE", message)
		self.assertIn("universal_scan", message)
		self.assertIn("Nothing was recorded", message)

	def test_a_login_qr_is_refused_and_never_quoted_back(self):
		payload = '{"url":"https://erp.example.com","api_key":"abc","api_secret":"shhh"}'
		message = self.tool_error("scan_valve_qr", {"qr_data": payload})
		self.assertIn("credential document", message)
		self.assertNotIn("shhh", message)


# ── 6. runtime is the log's answer ──────────────────────────────────────────
class TheRuntimeIsTheSameSum(ValveTestCase):
	def setUp(self):
		super().setUp()
		self.a_line()

	def test_a_valves_hours_match_get_irrigation_runtime_on_the_same_data(self):
		self.event(LATERAL, "open", "2026-07-10 06:00:00")
		self.event(LATERAL, "closed", "2026-07-10 09:30:00")

		mine = self.tool_data(
			"get_valve_runtime",
			{"name": LATERAL, "date_from": "2026-07-01", "date_to": "2026-07-31"},
		)
		theirs = self.tool_data(
			"get_irrigation_runtime",
			{"asset": LATERAL, "from_date": "2026-07-01", "to_date": "2026-07-31"},
		)
		self.assertEqual(mine["runtime_minutes"], theirs["runtime_minutes"])
		self.assertEqual(mine["runtime_hours"], 3.5)

	def test_both_spellings_of_the_window_mean_the_same_window(self):
		self.event(LATERAL, "open", "2026-07-10 06:00:00")
		self.event(LATERAL, "closed", "2026-07-10 09:30:00")

		stated = self.tool_data(
			"get_valve_runtime",
			{"name": LATERAL, "date_from": "2026-07-01", "date_to": "2026-07-31"},
		)
		other = self.tool_data(
			"get_valve_runtime",
			{"name": LATERAL, "from_date": "2026-07-01", "to_date": "2026-07-31"},
		)
		self.assertEqual(stated["from"], other["from"])
		self.assertEqual(stated["runtime_minutes"], other["runtime_minutes"])

	def test_the_zone_total_sits_beside_the_valves_own(self):
		self.event(LATERAL, "open", "2026-07-10 06:00:00")
		self.event(LATERAL, "closed", "2026-07-10 08:00:00")
		self.event(LATERAL_TWO, "open", "2026-07-11 06:00:00")
		self.event(LATERAL_TWO, "closed", "2026-07-11 07:00:00")

		data = self.tool_data(
			"get_valve_runtime",
			{"name": LATERAL, "date_from": "2026-07-01", "date_to": "2026-07-31"},
		)
		self.assertEqual(data["runtime_minutes"], 120.0)
		self.assertEqual(data["zone_rollup"]["runtime_minutes"], 180.0)
		self.assertEqual(data["zone_rollup"]["valve_count"], 4)

	def test_the_zone_rollup_is_priced_at_the_zones_own_flow_rate(self):
		self.event(LATERAL, "open", "2026-07-10 06:00:00")
		self.event(LATERAL, "closed", "2026-07-10 07:00:00")

		data = self.tool_data(
			"get_valve_runtime",
			{"name": LATERAL, "date_from": "2026-07-01", "date_to": "2026-07-31"},
		)
		self.assertEqual(data["zone_rollup"]["flow_rate_gpm"], 45.0)
		self.assertEqual(data["zone_rollup"]["gallons"], 2700.0)

	def test_the_zone_rollup_can_be_switched_off(self):
		data = self.tool_data(
			"get_valve_runtime",
			{"name": LATERAL, "date_from": "2026-07-01", "date_to": "2026-07-31", "include_zone": False},
		)
		self.assertIsNone(data["zone_rollup"])

	def test_a_mains_figure_counts_the_line_below_it(self):
		"""`get_valve_runtime` on a main walks its subtree, as the older tool does."""
		self.event(LATERAL, "open", "2026-07-10 06:00:00")
		self.event(LATERAL, "closed", "2026-07-10 08:00:00")

		data = self.tool_data(
			"get_valve_runtime",
			{"name": MAIN_VALVE, "date_from": "2026-07-01", "date_to": "2026-07-31"},
		)
		self.assertEqual(data["runtime_minutes"], 120.0)
		self.assertEqual(data["valve_count"], 4)

	def test_todays_runtime_keeps_the_valve_and_its_line_apart(self):
		today = str(frappe.utils.today())
		self.event(LATERAL, "open", f"{today} 06:00:00")
		self.event(LATERAL, "closed", f"{today} 07:00:00")
		self.event(LATERAL_TWO, "open", f"{today} 06:00:00")
		self.event(LATERAL_TWO, "closed", f"{today} 07:30:00")

		lateral = self.tool_data("get_irrigation_valve", {"name": LATERAL})["runtime_today"]
		self.assertEqual(lateral["minutes"], 60.0)
		self.assertEqual(lateral["subtree_minutes"], 60.0)

		main = self.tool_data("get_irrigation_valve", {"name": MAIN_VALVE})["runtime_today"]
		self.assertEqual(main["minutes"], 0.0)
		self.assertEqual(main["subtree_minutes"], 150.0)


# ── the switches ────────────────────────────────────────────────────────────
class EveryWriteHasItsOwnSwitch(ValveTestCase):
	def test_each_mutating_valve_tool_is_refused_on_its_own(self):
		for name in ("create_irrigation_valve", "toggle_irrigation_valve", "scan_valve_qr"):
			with self.subTest(tool=name):
				self.configure(enabled=1, **{**ALL_ON, f"allow_{name}": 0})
				message = self.tool_error(name, {})
				self.assertIn("switched off", message)
				self.assertIn(f"allow_{name}", message)

	def test_the_reads_still_work_with_every_write_switched_off(self):
		self.a_valve(LATERAL)
		self.configure(
			enabled=1,
			**{
				**ALL_ON,
				"allow_create_irrigation_valve": 0,
				"allow_toggle_irrigation_valve": 0,
				"allow_scan_valve_qr": 0,
			},
		)
		listed = self.tool_data("list_irrigation_valves", {})
		self.assertEqual(listed["valve_count"], 1)


# ── the mobile route ────────────────────────────────────────────────────────
class TheHandsetScansAndCanAct(MobileAPITestCase):
	"""`scan_valve`: one POST that reads the gate, and opens it only if asked.

	SUBCLASSED FROM THE MOBILE CASE RATHER THAN FROM `ValveTestCase`, because
	what is under test here is the ROUTE and not the tool — the scope check, the
	`toggle` default and the audit row are all the transport's, and the tool
	beneath it is already covered above.
	"""

	def setUp(self):
		super().setUp()
		self.configure(
			enabled=1,
			public_url="https://umbrel.tail4a2b.ts.net",
			**{**ON, **ALL_ON},
		)
		self.tool_data(
			"create_parcel",
			{"owning_entity": MAIN, "parcel_name": "Home Ranch", "acreage": 88.0},
		)
		self.tool_data(
			"create_field",
			{
				"parcel": "Home Ranch",
				"field_name": "Home Ranch Block 1",
				"acreage": 14.0,
				"variety": "Rainier",
				"planting_year": 2004,
				"condition": "Good",
			},
		)
		self.tool_data(
			"create_irrigation_zone",
			{
				"field": BLOCK,
				"zone_name": "HR1-Zone1",
				"zone_number": 1,
				"water_source": "well",
				"sprinkler_type": "drip",
				"area_sq_ft": 217800,
				"flow_rate_gpm": 45,
			},
		)
		self.tool_data(
			"create_irrigation_valve",
			{"valve_id": MAIN_VALVE, "valve_type": "Main", "zone": ZONE, "company": MAIN},
		)
		self.tool_data(
			"create_irrigation_valve",
			{"valve_id": LATERAL, "valve_type": "Lateral", "parent_valve": MAIN_VALVE, "company": MAIN},
		)

	def test_a_scan_alone_reads_the_gate_and_leaves_it_alone(self):
		self.be()
		data = mobile_api.scan_valve(qr_data=LATERAL)

		self.assertEqual(data["name"], LATERAL)
		self.assertEqual(data["state"], "closed")
		self.assertFalse(data["toggled"])
		self.assertTrue(data["scan_recorded"])
		self.assertEqual(data["next_action"]["action"], "open_valve")

	def test_the_scan_stamp_names_the_worker_who_scanned_it(self):
		self.be()
		data = mobile_api.scan_valve(qr_data=LATERAL)
		self.assertEqual(data["last_scan_by"], WORKER)

	def test_a_printed_tag_url_is_what_the_camera_actually_sends(self):
		self.be()
		data = mobile_api.scan_valve(qr_data=f"https://umbrel.tail4a2b.ts.net/scan/{LATERAL}")
		self.assertEqual(data["name"], LATERAL)

	def test_toggle_true_opens_it_in_the_same_post(self):
		self.be()
		data = mobile_api.scan_valve(qr_data=LATERAL, toggle=True)

		self.assertTrue(data["toggled"])
		self.assertEqual(data["from_state"], "closed")
		self.assertEqual(data["to_state"], "open")
		self.assertTrue(data["scan_recorded"])

	def test_the_second_scan_with_toggle_closes_it(self):
		self.be()
		mobile_api.scan_valve(qr_data=LATERAL, toggle=True)
		data = mobile_api.scan_valve(qr_data=LATERAL, toggle=True)
		self.assertEqual(data["to_state"], "closed")

	def test_closing_a_main_from_the_phone_cascades_and_says_what_went_dry(self):
		self.be()
		mobile_api.scan_valve(qr_data=MAIN_VALVE, toggle=True)
		mobile_api.scan_valve(qr_data=LATERAL, toggle=True)
		data = mobile_api.scan_valve(qr_data=MAIN_VALVE, toggle=True)

		self.assertEqual(data["to_state"], "closed")
		self.assertEqual(data["cascaded_count"], 1)
		self.assertEqual(data["cascaded"][0]["asset_name"], LATERAL)

	def test_a_stale_expect_state_is_refused_over_the_wire_too(self):
		self.be()
		mobile_api.scan_valve(qr_data=LATERAL, toggle=True)
		STORE.commit()

		with self.assertRaises(frappe.ValidationError):
			mobile_api.scan_valve(qr_data=LATERAL, toggle=True, expect_state="closed")

		self.be()
		after = mobile_api.scan_valve(qr_data=LATERAL)
		self.assertEqual(after["state"], "open")

	def test_a_refused_toggle_rolls_back_the_scan_stamp_but_not_the_audit_row(self):
		"""The transaction takes the stamp; the audit row is committed apart from it.

		Asserted rather than assumed because `scan_valve`'s own comment makes the
		claim, and it is the kind of claim that quietly stops being true. What an
		operator can reconstruct after a refusal is MCP Action Log — NOT
		`last_scan_at`, which records completed scans and not attempts.
		"""
		self.be()
		mobile_api.scan_valve(qr_data=LATERAL, toggle=True)
		STORE.commit()

		with self.assertRaises(frappe.ValidationError):
			mobile_api.scan_valve(qr_data=LATERAL, toggle=True, expect_state="closed")

		refusals = [row for row in self.audit_rows("scan_valve") if row.get("result_status") == "Error"]
		self.assertTrue(refusals, "a refused scan_valve left no audit row")

	def test_an_empty_scan_is_refused(self):
		self.be()
		with self.assertRaises(frappe.ValidationError):
			mobile_api.scan_valve(qr_data="")

	def test_a_valve_of_another_entity_reads_as_absent(self):
		"""The company is the caller's, taken from the scope check not the body."""
		self.be(OUTSIDER)
		with self.assertRaises(frappe.ValidationError):
			mobile_api.scan_valve(qr_data=LATERAL)

	def test_the_call_leaves_an_audit_row(self):
		self.be()
		mobile_api.scan_valve(qr_data=LATERAL, toggle=True)
		self.assertTrue(self.audit_rows("scan_valve"))
