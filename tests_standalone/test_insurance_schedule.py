# SPDX-License-Identifier: MIT
"""Tests for v0.77.0 — the capital-asset fields, and the schedule read off them."""

from .fixtures import MAIN, OTHER, V12TestCase
from .harness import STORE

ALL_ON = {
	"allow_register_asset": 1,
	"allow_update_registered_asset": 1,
	"allow_export_insurance_schedule": 1,
	"allow_get_asset_detail": 1,
	"allow_retire_asset": 1,
	"allow_list_assets": 1,
}

A_TRACTOR = {
	"name": "MC-Tractor-01",
	"asset_type": "Tractor",
	"serial_number": "1LV5075EPCB123456",
	"model": "John Deere 5075E",
	"acquired_on": "2019-04-02",
	"purchase_value": 41500,
	"replacement_value": 58000,
}


class InsuranceTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)
		STORE.singles["System Settings"] = {"time_zone": "America/Los_Angeles"}

	def an_asset(self, **overrides):
		payload = {"company": MAIN, **A_TRACTOR, **overrides}
		return self.tool_data("register_asset", payload)

	def a_photo(self, asset, name="tractor.jpg", private=1, uploaded="") -> str:
		"""One File row against an asset, as `attach_file_to_document` leaves it.

		`creation` is set explicitly rather than left to the store's default: the
		schedule orders photographs newest-first and a fixture whose rows all
		share one timestamp would pass whatever the ordering did.
		"""
		rows = STORE.tables.setdefault("File", {})
		index = len(rows) + 1
		docname = f"FILE-{index:04d}"
		rows[docname] = {
			"name": docname,
			"docstatus": 0,
			"file_name": name,
			"file_url": f"/private/files/{name}" if private else f"/files/{name}",
			"is_private": private,
			"file_size": 91234,
			"attached_to_doctype": "Asset Register",
			"attached_to_name": asset,
			"creation": uploaded or f"2026-07-{index:02d} 09:00:00",
		}
		return docname

	def schedule(self, **kw):
		return self.tool_data("export_insurance_schedule", {"company": MAIN, **kw})


# ── registration ──────────────────────────────────────────────────────────────
class RegisteringACapitalAsset(InsuranceTestCase):
	def test_the_insurance_fields_round_trip(self):
		data = self.an_asset()
		self.assertEqual(data["serial_number"], "1LV5075EPCB123456")
		self.assertEqual(data["model"], "John Deere 5075E")
		self.assertEqual(data["acquired_on"], "2019-04-02")
		self.assertEqual(data["purchase_value"], 41500.0)
		self.assertEqual(data["replacement_value"], 58000.0)

	def test_a_serial_number_is_searchable_on_the_register(self):
		"""The string an adjuster is holding is what they will look it up by."""
		self.an_asset()
		found = self.tool_data("list_assets", {"company": MAIN})
		self.assertEqual(found["assets"][0]["serial_number"], "1LV5075EPCB123456")

	def test_a_zero_value_is_not_the_same_as_no_value(self):
		"""A machine valued at nothing has been valued. An unvalued one has not,
		and a schedule that merged them would report both as "no cover stated"."""
		self.an_asset(name="MC-Old-01", replacement_value=0)
		row = self.tool_data("get_asset_detail", {"asset_name": "MC-Old-01"})
		self.assertEqual(row["replacement_value"], 0.0)

		self.an_asset(name="MC-New-01", replacement_value=None, purchase_value=None)
		row = self.tool_data("get_asset_detail", {"asset_name": "MC-New-01"})
		self.assertIsNone(row["replacement_value"])

	def test_the_new_equipment_types_are_registrable(self):
		for asset_type in ("Implement", "Vehicle"):
			with self.subTest(asset_type=asset_type):
				data = self.an_asset(name=f"MC-{asset_type}-01", asset_type=asset_type)
				self.assertEqual(data["asset_type"], asset_type)

	def test_the_fields_are_editable_afterwards(self):
		self.an_asset()
		data = self.tool_data(
			"update_registered_asset",
			{"asset_name": "MC-Tractor-01", "replacement_value": 61000, "model": "John Deere 5075E MFWD"},
		)
		self.assertEqual(data["replacement_value"], 61000.0)
		self.assertIn("replacement_value", data["changed"])


# ── parent_asset ──────────────────────────────────────────────────────────────
class TheParentGoesInAtRegistration(InsuranceTestCase):
	def test_parent_asset_sets_the_hierarchy(self):
		self.tool_data("register_asset", {"name": "MC-Main-01", "asset_type": "Irrigation Valve", "company": MAIN})
		data = self.tool_data("register_asset", {
			"name": "MC-Lat-A", "asset_type": "Irrigation Valve", "company": MAIN,
			"parent_asset": "MC-Main-01",
		})
		self.assertEqual(data["parent_asset"], "MC-Main-01")
		self.assertEqual(data["location"], "MC-Main-01")

	def test_location_still_works_and_means_the_same_thing(self):
		"""The column has been `location` since v0.25.0. Renaming it would break
		every stored filter and every client already sending it."""
		self.tool_data("register_asset", {"name": "MC-Main-02", "asset_type": "Irrigation Valve", "company": MAIN})
		data = self.tool_data("register_asset", {
			"name": "MC-Lat-B", "asset_type": "Irrigation Valve", "company": MAIN,
			"location": "MC-Main-02",
		})
		self.assertEqual(data["parent_asset"], "MC-Main-02")

	def test_the_two_spellings_disagreeing_is_refused(self):
		"""There is no reading of that call which is not somebody's mistake, and
		picking a winner silently puts a valve under the wrong turnout."""
		self.tool_data("register_asset", {"name": "MC-Main-03", "asset_type": "Irrigation Valve", "company": MAIN})
		self.tool_data("register_asset", {"name": "MC-Main-04", "asset_type": "Irrigation Valve", "company": MAIN})
		error = self.tool_error("register_asset", {
			"name": "MC-Lat-C", "asset_type": "Irrigation Valve", "company": MAIN,
			"parent_asset": "MC-Main-03", "location": "MC-Main-04",
		})
		self.assertIn("two names for one column", error)

	def test_an_unregistered_parent_is_refused_by_name(self):
		error = self.tool_error("register_asset", {
			"name": "MC-Lat-D", "asset_type": "Irrigation Valve", "company": MAIN,
			"parent_asset": "MC-Nope",
		})
		self.assertIn("MC-Nope", error)
		self.assertIn("registered first", error)

	def test_the_parent_can_be_changed_later_under_either_name(self):
		self.tool_data("register_asset", {"name": "MC-Tractor-09", "asset_type": "Tractor", "company": MAIN})
		self.an_asset(name="MC-Disc-01", asset_type="Implement")
		data = self.tool_data(
			"update_registered_asset", {"asset_name": "MC-Disc-01", "parent_asset": "MC-Tractor-09"}
		)
		self.assertEqual(data["parent_asset"], "MC-Tractor-09")


# ── the photograph ────────────────────────────────────────────────────────────
class ThePhotographAtRegistration(InsuranceTestCase):
	def test_an_uploaded_file_is_attached_to_the_new_asset(self):
		rows = STORE.tables.setdefault("File", {})
		rows["FILE-0001"] = {
			"name": "FILE-0001", "docstatus": 0, "file_name": "vin.jpg",
			"file_url": "/private/files/vin.jpg", "is_private": 1,
		}
		data = self.an_asset(photo_file_token="FILE-0001")
		self.assertEqual(data["photo_attached"], "FILE-0001")
		self.assertEqual(rows["FILE-0001"]["attached_to_doctype"], "Asset Register")
		self.assertEqual(rows["FILE-0001"]["attached_to_name"], "MC-Tractor-01")

	def test_an_unknown_token_does_not_lose_the_registration(self):
		"""The asset is the record; the photograph is evidence about it. Losing a
		tractor's registration because its photo could not be attached would be
		the wrong trade."""
		data = self.an_asset(photo_file_token="FILE-nope")
		self.assertEqual(data["name"], "MC-Tractor-01")
		self.assertIsNone(data["photo_attached"])
		self.assertIn("FILE-nope", data["photo_error"])
		self.assertIn("WAS registered", data["photo_error"])

	def test_registering_without_a_photo_says_nothing_about_one(self):
		data = self.an_asset()
		self.assertIsNone(data["photo_attached"])
		self.assertNotIn("photo_error", data)


# ── the schedule ──────────────────────────────────────────────────────────────
class TheSchedule(InsuranceTestCase):
	def test_a_capital_asset_appears_with_every_column_an_insurer_asks_for(self):
		self.an_asset()
		self.a_photo("MC-Tractor-01")
		row = self.schedule()["schedule"][0]

		self.assertEqual(row["asset"], "MC-Tractor-01")
		self.assertEqual(row["asset_type"], "Tractor")
		self.assertEqual(row["serial_number"], "1LV5075EPCB123456")
		self.assertEqual(row["model"], "John Deere 5075E")
		self.assertEqual(row["acquired_on"], "2019-04-02")
		self.assertEqual(row["insured_value"], 58000.0)
		self.assertEqual(row["photo_url"], "/private/files/tractor.jpg")

	def test_a_valve_is_not_on_an_equipment_schedule(self):
		"""A valve is a fitting and a block is land. A default that included them
		would make the first thing anybody did with this tool be filtering it."""
		self.an_asset()
		self.tool_data("register_asset", {"name": "MC-Valve-05", "asset_type": "Irrigation Valve", "company": MAIN})
		self.tool_data("register_asset", {"name": "MC-Block-A", "asset_type": "Block", "company": MAIN})
		self.assertEqual([row["asset"] for row in self.schedule()["schedule"]], ["MC-Tractor-01"])

	def test_asset_types_widens_it(self):
		self.an_asset()
		self.tool_data("register_asset", {"name": "MC-Cold-01", "asset_type": "Cold Storage", "company": MAIN})
		found = self.schedule(asset_types=["Tractor", "Cold Storage"])
		self.assertEqual(sorted(row["asset"] for row in found["schedule"]), ["MC-Cold-01", "MC-Tractor-01"])

	def test_an_unknown_asset_type_is_refused_with_the_real_list(self):
		error = self.tool_error(
			"export_insurance_schedule", {"company": MAIN, "asset_types": ["Combine"]}
		)
		self.assertIn("Combine", error)
		self.assertIn("Irrigation Valve", error)

	def test_a_retired_machine_is_left_off_by_default(self):
		"""A sold tractor is not insured, and a schedule carrying it is paying
		for it."""
		self.an_asset()
		self.an_asset(name="MC-Tractor-02")
		self.tool_data("retire_asset", {"asset_name": "MC-Tractor-02"})

		self.assertEqual([row["asset"] for row in self.schedule()["schedule"]], ["MC-Tractor-01"])
		widened = self.schedule(include_retired=True)
		self.assertEqual(len(widened["schedule"]), 2)

	def test_every_photograph_is_reported_newest_first(self):
		self.an_asset()
		self.a_photo("MC-Tractor-01", name="old.jpg")
		self.a_photo("MC-Tractor-01", name="new.jpg")
		row = self.schedule()["schedule"][0]
		self.assertEqual(row["photo_count"], 2)
		self.assertEqual(row["photos"][0]["file_name"], "new.jpg")
		self.assertEqual(row["photo_url"], "/private/files/new.jpg")

	def test_a_private_url_is_reported_as_stored(self):
		"""Rewriting it to something public here would be this tool quietly
		deciding to publish a farm's equipment photographs."""
		self.an_asset()
		self.a_photo("MC-Tractor-01")
		photo = self.schedule()["schedule"][0]["photos"][0]
		self.assertTrue(photo["file_url"].startswith("/private/files/"))
		self.assertTrue(photo["is_private"])

	def test_where_it_lives_is_the_chain_of_assets_above_it(self):
		self.tool_data("register_asset", {"name": "MC-Ranch", "asset_type": "General", "company": MAIN})
		self.tool_data("register_asset", {
			"name": "MC-Shed", "asset_type": "Storage", "company": MAIN, "parent_asset": "MC-Ranch",
		})
		self.an_asset(parent_asset="MC-Shed")
		row = self.schedule()["schedule"][0]
		self.assertEqual(row["location_path"], ["MC-Ranch", "MC-Shed"])
		self.assertEqual(row["location"], "MC-Ranch › MC-Shed")
		self.assertEqual(row["parent_asset"], "MC-Shed")


# ── value basis ───────────────────────────────────────────────────────────────
class WhatItIsInsuredFor(InsuranceTestCase):
	def test_replacement_value_is_what_a_schedule_is_written_against(self):
		self.an_asset()
		row = self.schedule()["schedule"][0]
		self.assertEqual(row["insured_value"], 58000.0)
		self.assertEqual(row["value_basis"], "replacement")

	def test_purchase_value_is_the_fallback_and_the_row_says_so(self):
		"""A 2011 price presented as today's cover understates the loss on
		exactly the machines most likely to be old."""
		self.an_asset(replacement_value=None)
		row = self.schedule()["schedule"][0]
		self.assertEqual(row["insured_value"], 41500.0)
		self.assertEqual(row["value_basis"], "purchase")
		self.assertIn("MC-Tractor-01", self.schedule()["gaps"]["valued_at_purchase_price"])

	def test_a_machine_with_neither_is_reported_unvalued_rather_than_zero(self):
		self.an_asset(replacement_value=None, purchase_value=None)
		row = self.schedule()["schedule"][0]
		self.assertIsNone(row["insured_value"])
		self.assertEqual(row["value_basis"], "none")

	def test_the_total_adds_up_the_insured_values(self):
		self.an_asset()
		self.an_asset(name="MC-Tractor-02", replacement_value=12000)
		found = self.schedule()
		self.assertEqual(found["total_insured_value"], 70000.0)
		self.assertEqual(found["valued_asset_count"], 2)

	def test_a_total_across_two_companies_is_withheld_with_the_reason(self):
		"""Equipment held in two entities is insured on two policies, and one
		figure across them is wrong for both."""
		self.an_asset()
		self.an_asset(name="OTHER-Tractor-01", company=OTHER)
		found = self.tool_data("export_insurance_schedule", {})
		self.assertIsNone(found["total_insured_value"])
		self.assertIn("two policies", found["total_withheld_because"])
		self.assertEqual(found["asset_count"], 2)

	def test_the_breakdown_by_type_adds_up(self):
		self.an_asset()
		self.an_asset(name="MC-Disc-01", asset_type="Implement", replacement_value=9000)
		found = self.schedule()
		self.assertEqual(found["by_asset_type"]["Tractor"]["insured_value"], 58000.0)
		self.assertEqual(found["by_asset_type"]["Implement"]["count"], 1)


# ── gaps ──────────────────────────────────────────────────────────────────────
class WhatTheScheduleCannotAnswer(InsuranceTestCase):
	def test_a_complete_schedule_says_so(self):
		self.an_asset()
		self.a_photo("MC-Tractor-01")
		self.assertTrue(self.schedule()["gaps"]["complete"])

	def test_every_missing_serial_value_photo_and_date_is_itemised(self):
		"""Nine machines without a serial is an afternoon's work, and the
		afternoon is invisible until an adjuster asks."""
		self.an_asset(
			name="MC-Tractor-03",
			serial_number=None,
			replacement_value=None,
			purchase_value=None,
			acquired_on=None,
		)
		gaps = self.schedule()["gaps"]
		self.assertEqual(gaps["missing_serial_number"], ["MC-Tractor-03"])
		self.assertEqual(gaps["missing_value"], ["MC-Tractor-03"])
		self.assertEqual(gaps["missing_photo"], ["MC-Tractor-03"])
		self.assertEqual(gaps["missing_acquired_on"], ["MC-Tractor-03"])
		self.assertFalse(gaps["complete"])

	def test_a_gap_is_countable_so_it_can_be_worked_through(self):
		"""Lists AND counts: a client renders the number and a person opens the
		list, and deriving one from the other on every screen is where the two
		stop agreeing."""
		self.an_asset(name="MC-Tractor-04", serial_number=None)
		self.an_asset(name="MC-Tractor-05")
		gaps = self.schedule()["gaps"]
		self.assertEqual(gaps["missing_serial_number_count"], 1)
		self.assertEqual(len(gaps["missing_serial_number"]), gaps["missing_serial_number_count"])


# ── timezone ──────────────────────────────────────────────────────────────────
class TheScheduleSaysWhichClock(InsuranceTestCase):
	def test_the_zone_block_is_on_the_response(self):
		self.an_asset()
		found = self.schedule()
		self.assertEqual(found["timezone"], "America/Los_Angeles")
		self.assertEqual(found["stored_timezone"], "America/Los_Angeles")
		self.assertTrue(found["generated_at_local"].endswith(("-07:00", "-08:00")))

	def test_a_requested_zone_moves_the_clock(self):
		self.an_asset()
		found = self.schedule(timezone="America/New_York")
		self.assertEqual(found["timezone"], "America/New_York")
		self.assertTrue(found["generated_at_local"].endswith(("-04:00", "-05:00")))

	def test_acquired_on_stays_a_date_with_no_zone_on_it(self):
		"""A date has no instant in it and midnight would be invented."""
		self.an_asset()
		row = self.schedule()["schedule"][0]
		self.assertEqual(row["acquired_on"], "2019-04-02")
		self.assertNotIn("acquired_on_local", row)

	def test_an_unknown_zone_is_refused(self):
		self.an_asset()
		error = self.tool_error(
			"export_insurance_schedule", {"company": MAIN, "timezone": "Pacific/Nowhere"}
		)
		self.assertIn("Pacific/Nowhere", error)


class TheSwitch(InsuranceTestCase):
	def test_it_is_on_by_default_because_it_is_a_read(self):
		self.configure(enabled=1, allow_register_asset=1)
		self.an_asset()
		self.configure(enabled=1)
		self.assertEqual(self.tool_data("export_insurance_schedule", {"company": MAIN})["asset_count"], 1)

	def test_it_writes_nothing(self):
		self.an_asset()
		before = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		self.schedule()
		after = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		after.pop("MCP Action Log", None)
		before.pop("MCP Action Log", None)
		self.assertEqual(before, after)
