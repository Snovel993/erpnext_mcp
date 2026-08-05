# SPDX-License-Identifier: MIT
"""The crew's track — v0.32.0's two tools and the doctype behind them.

THE CLAIM BEHIND THE FEATURE is that a shift already says where the crew was
supposed to be and nothing says where they went. `location` is free text in the
words the crew uses, `farm_location_gps` is one coordinate for the weather fetch,
and neither answers "which block was the crew in at 09:40 while the re-entry
interval was running" or "were the hours somebody is claiming worked where the
work was". A track answers both, and only a track does.

SEVEN CLAIMS.

1. `TheBreadcrumbIsAppended` — one fix per call, stored with the company derived
   from the shift and the employee's name snapshotted, and the resolution-10 H3
   cell computed so a track can be joined against a block's stored coverage.

2. `TheTimestampIsWhenItWasTaken` — the ordering test, and it is the one that
   matters most. A phone out of signal posts an hour of breadcrumbs the moment
   the bars come back, so a track read in arrival order draws the crew standing
   still and then teleporting. The fixture below inserts them deliberately out of
   order.

3. `WhatItRefuses` — coordinates off Earth (a latitude past 90 is the pair the
   wrong way round, which is the only version of that mistake a computer can
   catch), a missing position, and an employee belonging to another entity.

4. `WhatItMerelyRecords` — a terrible accuracy, and a fix outside the shift's own
   span. Both are kept and reported: a fix under a canopy in a canyon is the only
   record the crew was there at all, and a phone that could not reach the site
   until the evening is posting about a shift already closed.

5. `TheGapsAreReportedAndNothingIsInterpolated` — a silence longer than ten
   minutes is named with its length. Nothing invents a position for the middle of
   one, because an invented position on a record read in a wage dispute is the
   worst thing this app could put on a map.

6. `TheGuards` — the role gate, the company scope, and the two switches. The
   write is OFF out of the box and the read is ON, and both are asserted against
   the shipped defaults rather than against a fixture.

7. `TheShiftFormKnowsWithoutLoadingIt` — get_shift reports a COUNT and not the
   track. A shift with a fix every two minutes carries hundreds of points, and
   returning them on every read of every shift would make each one pay for a map
   nobody asked to see.
"""

import frappe

from erpnext_mcp import shifts

from .fixtures import MAIN, OTHER
from .harness import ROLES, STORE, set_roles
from .test_shifts import CREW, WORKER, ShiftTestCase, at

#: The two v0.32.0 switches, plus everything the fixture needs to open a shift.
TRACKING_ON = {"allow_log_shift_location": 1, "allow_get_shift_track": 1}

#: A point inside Block 7 North, and one about four hundred metres east of it.
HERE = {"latitude": 45.6015, "longitude": -121.1780}
THERE = {"latitude": 45.6015, "longitude": -121.1730}


class TrackingTestCase(ShiftTestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **{**self._on(), **TRACKING_ON})

	def _on(self) -> dict:
		from .test_shifts import ON

		return dict(ON)

	def a_shift(self, **overrides) -> str:
		return self.start(**overrides)["name"]

	def log(self, shift: str, **overrides):
		payload = {"shift": shift, **HERE, "timestamp": at(7)}
		payload.update(overrides)
		return self.tool_data("log_shift_location", payload)

	def log_error(self, shift: str, **overrides):
		payload = {"shift": shift, **HERE, "timestamp": at(7)}
		payload.update(overrides)
		return self.tool_error("log_shift_location", payload)

	def track(self, shift: str, **overrides):
		return self.tool_data("get_shift_track", {"shift": shift, **overrides})

	def stored(self) -> list:
		return [dict(row) for row in STORE.rows(shifts.LOCATION_DOCTYPE)]


# ── 1 ───────────────────────────────────────────────────────────────────────
class TheBreadcrumbIsAppended(TrackingTestCase):
	def test_one_call_writes_one_row(self):
		shift = self.a_shift()
		data = self.log(shift)
		self.assertEqual(len(self.stored()), 1)
		self.assertEqual(data["logs_on_this_shift"], 1)
		self.assertEqual(data["lat"], 45.6015)
		self.assertEqual(data["lon"], -121.178)

	def test_the_company_is_derived_from_the_shift_and_never_taken(self):
		"""It is what scopes a track. A User Permission on Company reaches this
		doctype because this column links to one, and a track that carried no
		company would be readable by every scoped account on the site."""
		shift = self.a_shift()
		self.log(shift)
		self.assertEqual(self.stored()[0]["company"], MAIN)

	def test_the_employee_name_is_snapshotted_at_write_time(self):
		"""A lookup at report time answers with today's name. A track read in a
		wage dispute is entitled to who the person was then."""
		shift = self.a_shift()
		self.log(shift, employee=WORKER)
		row = self.stored()[0]
		self.assertEqual(row["employee"], WORKER)
		self.assertEqual(row["employee_name"], frappe.db.get_value("Employee", WORKER, "employee_name"))

	def test_the_h3_cell_is_computed_so_a_track_can_be_joined_against_a_block(self):
		"""Resolution 10, which is the finest a boundary is indexed at — every
		coarser stored resolution is its parent, so one column answers at all
		five. Skipped on a bench with no h3, which is itself the contract: the
		coordinates are the evidence and the cell is only the index over them."""
		from erpnext_mcp import geo

		shift = self.a_shift()
		self.log(shift)
		cell = self.stored()[0].get("h3_cell")
		if not geo.available():
			self.assertFalse(cell)
			return
		self.assertTrue(cell)
		self.assertEqual(geo.cell_resolution(cell), 10)

	def test_the_source_defaults_to_the_phone(self):
		shift = self.a_shift()
		self.log(shift)
		self.assertEqual(self.stored()[0]["source"], "iOS")

	def test_a_typed_coordinate_can_say_so(self):
		shift = self.a_shift()
		data = self.log(shift, source="Manual", notes="paced off the corner post")
		self.assertEqual(data["source"], "Manual")
		self.assertEqual(data["notes"], "paced off the corner post")

	def test_lat_and_lon_are_accepted_as_the_names_the_phone_uses(self):
		"""One spelling refused is one round trip for nothing, and
		find_fields_containing_point already calls them lat and lon."""
		shift = self.a_shift()
		data = self.tool_data("log_shift_location", {"shift": shift, "lat": 45.6015, "lon": -121.178})
		self.assertEqual(data["lat"], 45.6015)

	def test_nothing_edits_an_existing_row(self):
		"""Two calls are two breadcrumbs. A fix that overwrote the last one would
		make the track a position rather than a history."""
		shift = self.a_shift()
		self.log(shift, timestamp=at(7))
		self.log(shift, timestamp=at(8), **THERE)
		self.assertEqual(len(self.stored()), 2)


# ── 2 ───────────────────────────────────────────────────────────────────────
class TheTimestampIsWhenItWasTaken(TrackingTestCase):
	"""THE ORDERING TEST. A phone out of signal in a canyon posts an hour of
	breadcrumbs the moment the bars come back; a track sorted by insertion shows
	the crew standing still all morning where the signal returned and then
	teleporting across the farm."""

	def test_the_track_comes_back_in_the_order_the_fixes_were_taken(self):
		shift = self.a_shift()
		# Deliberately posted out of order — 09:00 first, then the 07:00 and 08:00
		# ones the phone had been holding.
		self.log(shift, timestamp=at(9))
		self.log(shift, timestamp=at(7))
		self.log(shift, timestamp=at(8))
		data = self.track(shift)
		self.assertEqual([point["timestamp"] for point in data["track"]], [at(7), at(8), at(9)])
		self.assertEqual(data["first_fix"], at(7))
		self.assertEqual(data["last_fix"], at(9))

	def test_the_count_is_the_count(self):
		shift = self.a_shift()
		for hour in (7, 8, 9):
			self.log(shift, timestamp=at(hour))
		self.assertEqual(self.track(shift)["count"], 3)

	def test_a_shift_with_no_track_says_so_rather_than_looking_broken(self):
		"""The ordinary case for a shift worked before the phones were logging,
		and it is NOT a gap in the compliance record."""
		data = self.track(self.a_shift())
		self.assertEqual(data["count"], 0)
		self.assertEqual(data["track"], [])
		self.assertIn("not a gap in the compliance record", data["note"])

	def test_it_can_be_narrowed_to_one_person(self):
		shift = self.a_shift()
		self.log(shift, employee=WORKER, timestamp=at(7))
		self.log(shift, employee=CREW[0], timestamp=at(8))
		data = self.track(shift, employee=WORKER)
		self.assertEqual(data["count"], 1)
		self.assertEqual(data["track"][0]["employee"], WORKER)

	def test_it_reports_which_devices_contributed(self):
		shift = self.a_shift()
		self.log(shift, employee=WORKER, timestamp=at(7))
		self.log(shift, employee=CREW[0], timestamp=at(8))
		self.assertEqual(self.track(shift)["employees_tracked"], sorted([WORKER, CREW[0]]))

	def test_a_fix_with_no_employee_is_kept_and_does_not_invent_one(self):
		"""A foreman's phone in the truck traces the crew's day. Refusing it
		because it names nobody in particular would throw away the only track most
		shifts will ever have."""
		shift = self.a_shift()
		self.log(shift)
		point = self.track(shift)["track"][0]
		self.assertIsNone(point["employee"])
		self.assertEqual(self.track(shift)["employees_tracked"], [])


# ── 3 ───────────────────────────────────────────────────────────────────────
class WhatItRefuses(TrackingTestCase):
	def test_a_latitude_past_ninety_is_the_pair_the_wrong_way_round(self):
		shift = self.a_shift()
		message = self.log_error(shift, latitude=-121.178, longitude=45.6015)
		self.assertIn("not a point on Earth", message)
		self.assertIn("wrong way round", message)
		self.assertFalse(self.stored())

	def test_a_longitude_off_the_planet_is_refused(self):
		shift = self.a_shift()
		self.assertIn("not a point on Earth", self.log_error(shift, longitude=-361))

	def test_a_missing_position_is_refused_by_name(self):
		shift = self.a_shift()
		message = self.tool_error("log_shift_location", {"shift": shift, "latitude": 45.6})
		self.assertIn("longitude is required", message)
		self.assertIn("Nothing was created", message)

	def test_an_unknown_shift_is_refused(self):
		self.assertIn("no Farm Shift called", self.log_error("SHIFT-2026-9999"))

	def test_an_employee_from_another_entity_is_refused(self):
		"""A breadcrumb filed against another company's crew is evidence in the
		wrong packet."""
		STORE.seed(
			"Employee",
			[
				{
					"name": "HR-EMP-09001",
					"employee_name": "Outsider",
					"status": "Active",
					"date_of_joining": "2026-06-01",
					"company": OTHER,
				}
			],
		)
		shift = self.a_shift()
		message = self.log_error(shift, employee="HR-EMP-09001")
		self.assertIn(OTHER, message)
		self.assertIn("wrong packet", message)
		self.assertFalse(self.stored())


# ── 4 ───────────────────────────────────────────────────────────────────────
class WhatItMerelyRecords(TrackingTestCase):
	def test_a_terrible_accuracy_is_kept_and_noted(self):
		"""A fix under a canopy in a canyon reads badly and is still the only
		record the crew was there. A threshold that dropped it would delete the
		evidence from precisely the ground that is hardest to work."""
		shift = self.a_shift()
		data = self.log(shift, accuracy_meters=310)
		self.assertEqual(len(self.stored()), 1)
		self.assertEqual(data["accuracy_meters"], 310.0)
		self.assertTrue(any("accuracy" in warning for warning in data["warnings"]))

	def test_a_good_accuracy_is_not_complained_about(self):
		shift = self.a_shift()
		self.assertEqual(self.log(shift, accuracy_meters=4.5)["warnings"], [])

	def test_a_fix_before_the_shift_started_is_kept_and_reported(self):
		shift = self.a_shift()
		data = self.log(shift, timestamp=at(4))
		self.assertEqual(len(self.stored()), 1)
		self.assertTrue(any("before the shift started" in w for w in data["warnings"]))

	def test_a_fix_after_a_closed_shift_ended_is_kept_and_reported(self):
		"""A phone that could not reach the site until the evening is posting
		about a shift the foreman has already closed, and refusing those would
		throw away the evidence that is hardest to collect."""
		shift = self.a_shift()
		self.close(shift)
		data = self.log(shift, timestamp=at(18))
		self.assertEqual(len(self.stored()), 1)
		self.assertFalse(data["shift_open"])
		self.assertTrue(any("after the shift ended" in w for w in data["warnings"]))

	def test_a_closed_shift_still_takes_breadcrumbs(self):
		shift = self.a_shift()
		self.close(shift)
		self.log(shift, timestamp=at(14))
		self.assertEqual(self.track(shift)["count"], 1)


# ── 5 ───────────────────────────────────────────────────────────────────────
class TheGapsAreReportedAndNothingIsInterpolated(TrackingTestCase):
	def test_a_silence_longer_than_ten_minutes_is_named_with_its_length(self):
		shift = self.a_shift()
		self.log(shift, timestamp=at(7, 0))
		self.log(shift, timestamp=at(7, 5), **THERE)
		self.log(shift, timestamp=at(8, 30))
		gaps = self.track(shift)["gaps"]
		self.assertEqual(len(gaps), 1)
		self.assertEqual(gaps[0]["from"], at(7, 5))
		self.assertEqual(gaps[0]["to"], at(8, 30))
		self.assertEqual(gaps[0]["minutes"], 85.0)

	def test_a_steady_cadence_reports_no_gaps(self):
		shift = self.a_shift()
		for minute in (0, 2, 4, 6):
			self.log(shift, timestamp=at(7, minute))
		self.assertEqual(self.track(shift)["gaps"], [])

	def test_the_track_holds_exactly_the_fixes_that_were_posted(self):
		"""NOTHING IS INTERPOLATED. An invented position on a record read in a
		wage dispute or a re-entry-interval question is the worst thing this app
		could put on a map, so an 85-minute gap stays two points and not
		eighty-five."""
		shift = self.a_shift()
		self.log(shift, timestamp=at(7, 0))
		self.log(shift, timestamp=at(8, 30))
		self.assertEqual(self.track(shift)["count"], 2)


# ── 6 ───────────────────────────────────────────────────────────────────────
class TheGuards(TrackingTestCase):
	TOOLS = (
		("log_shift_location", {"shift": "SHIFT-2026-0001", **HERE}),
		("get_shift_track", {"shift": "SHIFT-2026-0001"}),
	)

	def test_the_write_is_off_out_of_the_box_and_the_read_is_on(self):
		"""Asserted against the shipped DocType defaults, not against a fixture. A
		location track is a record of where people were, and turning that on is a
		decision an operator takes on purpose; reading one cannot create one."""
		defaults = self.configure()
		self.assertEqual(str(defaults["allow_log_shift_location"]), "0")
		self.assertEqual(str(defaults["allow_get_shift_track"]), "1")

	def test_each_switch_turns_its_own_tool_off(self):
		shift = self.a_shift()
		for name, arguments in self.TOOLS:
			with self.subTest(tool=name):
				self.configure(enabled=1, **{**self._on(), **TRACKING_ON, f"allow_{name}": 0})
				message = self.tool_error(name, {**arguments, "shift": shift})
				self.assertIn(f"allow_{name}", message)
				self.assertIn("switched off", message)

	def test_an_account_with_no_hr_role_is_refused_by_both(self):
		shift = self.a_shift()
		set_roles(frappe.session.user, ["Accounts Manager"])
		for name, arguments in self.TOOLS:
			with self.subTest(tool=name):
				self.assertIn(
					"may not change the personnel register",
					self.tool_error(name, {**arguments, "shift": shift}),
				)

	def test_a_scoped_account_cannot_read_another_entity_s_track(self):
		shift = self.a_shift()
		self.log(shift)
		STORE.seed(
			"User Permission",
			[
				{
					"name": "UP-TRACK-1",
					"user": frappe.session.user,
					"allow": "Company",
					"for_value": OTHER,
				}
			],
		)
		message = self.tool_error("get_shift_track", {"shift": shift})
		self.assertIn("no access to company", message)
		self.assertIn(MAIN, message)

	def test_the_master_switch_takes_both_with_it(self):
		"""Off, the endpoint behaves as if it does not exist — a 404 and not a
		403, so a scan cannot tell a site that has this app switched off from one
		that never installed it."""
		shift = self.a_shift()
		self.configure(enabled=0)
		for name, arguments in self.TOOLS:
			with self.subTest(tool=name):
				_, status = self.call(
					"tools/call", {"name": name, "arguments": {**arguments, "shift": shift}}
				)
				self.assertEqual(status, 404)

	def test_the_role_list_is_unchanged_by_this_module(self):
		"""Guard on the fixture rather than on the app: every test above runs
		under whatever roles ShiftTestCase set, and a module that quietly widened
		them would make its own guards meaningless."""
		self.assertTrue(ROLES)


# ── 7 ───────────────────────────────────────────────────────────────────────
class TheShiftFormKnowsWithoutLoadingIt(TrackingTestCase):
	def test_get_shift_reports_a_count_and_not_the_track(self):
		"""A shift with a fix every two minutes carries hundreds of points.
		Returning them on every read of every shift would make each one pay for a
		map nobody asked to see."""
		shift = self.a_shift()
		for hour in (7, 8, 9):
			self.log(shift, timestamp=at(hour))
		data = self.tool_data("get_shift", {"name": shift})
		self.assertEqual(data["location_log_count"], 3)
		self.assertNotIn("track", data)

	def test_an_untracked_shift_reports_zero_rather_than_nothing(self):
		data = self.tool_data("get_shift", {"name": self.a_shift()})
		self.assertEqual(data["location_log_count"], 0)
