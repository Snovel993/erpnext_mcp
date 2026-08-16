# SPDX-License-Identifier: MIT
"""Tests for v0.77.0 — saying which six o'clock.

THE TWO FACTS THIS FILE IS PROTECTING are easy to break by tidying:

  * the milliseconds, which look like noise and are the difference between the
    shipped handset parsing a timestamp and returning nil for it, and
  * the storage, which is NOT UTC and must not become UTC in the new columns
    while the rest of the app writes site-local into the old ones.

Both have a test that fails loudly with the reason in the message, because the
next person to read `2026-07-24T06:00:00.000-07:00` will want to remove the
`.000`.
"""

import datetime
import unittest
import zoneinfo

from erpnext_mcp import timezones
from erpnext_mcp.errors import ToolError

from .fixtures import V12TestCase
from .harness import STORE

PACIFIC = "America/Los_Angeles"


def _seed_zone(name: str) -> None:
	"""Set this fake site's System Settings.time_zone, as a real site has."""
	STORE.singles["System Settings"] = {"time_zone": name}


class TheZoneComesFromTheSite(V12TestCase):
	def test_a_configured_zone_is_used_and_named(self):
		_seed_zone(PACIFIC)
		zone, source = timezones.site_timezone()
		self.assertEqual(zone, PACIFIC)
		self.assertIn("System Settings", source)

	def test_an_unset_zone_falls_back_to_utc_and_says_it_fell_back(self):
		"""A farm in the Pacific being told +00:00 by a server that assumed is the
		bug this module exists to end. Guessing America/Los_Angeles instead would
		be the same bug pointed at a different farm."""
		STORE.singles.pop("System Settings", None)
		zone, source = timezones.site_timezone()
		self.assertEqual(zone, "UTC")
		self.assertIn("not set", source)
		self.assertIn("America/Los_Angeles", source)

	def test_a_nonsense_zone_is_reported_rather_than_crashing_the_read(self):
		_seed_zone("Pacific Standard Time")
		zone, source = timezones.site_timezone()
		self.assertEqual(zone, "UTC")
		self.assertIn("Pacific Standard Time", source)
		self.assertIn("fix the setting", source)


class TheOffsetIsSpelledOut(V12TestCase):
	def setUp(self):
		super().setUp()
		_seed_zone(PACIFIC)

	def test_a_summer_timestamp_carries_the_summer_offset(self):
		self.assertEqual(
			timezones.local("2026-07-24 06:00:00", PACIFIC, PACIFIC),
			"2026-07-24T06:00:00.000-07:00",
		)

	def test_a_winter_timestamp_carries_the_winter_offset(self):
		"""Pacific is -07:00 in July and -08:00 in January. A single site-wide
		offset would have one half of a year-long report an hour out, which is
		why there is no `utc_offset` key anywhere in a payload."""
		self.assertEqual(
			timezones.local("2026-01-15 06:00:00", PACIFIC, PACIFIC),
			"2026-01-15T06:00:00.000-08:00",
		)

	def test_the_milliseconds_are_there_because_the_handset_requires_them(self):
		"""`FrappeDate.parse` reaches for ISO8601DateFormatter with
		`.withFractionalSeconds`, and that option is a REQUIREMENT rather than a
		tolerance: without the `.000` the shipped app returns nil for the whole
		field. Do not tidy them away."""
		rendered = timezones.local("2026-07-24 06:00:00", PACIFIC, PACIFIC)
		self.assertIn(".000", rendered)
		self.assertRegex(rendered, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}$")

	def test_rendering_in_another_zone_moves_the_clock_and_not_the_instant(self):
		"""An office in Denver reading a Pacific farm's valve log sees eight
		o'clock where the worker at the valve saw seven, and both are one moment."""
		pacific = timezones.local("2026-07-24 07:00:00", PACIFIC, PACIFIC)
		denver = timezones.local("2026-07-24 07:00:00", "America/Denver", PACIFIC)
		self.assertEqual(denver, "2026-07-24T08:00:00.000-06:00")
		self.assertEqual(
			datetime.datetime.fromisoformat(pacific),
			datetime.datetime.fromisoformat(denver),
		)

	def test_a_date_gets_no_local_twin(self):
		"""A date has no instant in it and midnight is a time this would be
		inventing. `acquired_on` is a Date column and stays one."""
		self.assertIsNone(timezones.local("2026-07-24", PACIFIC, PACIFIC))

	def test_an_empty_or_unreadable_value_is_none_rather_than_an_exception(self):
		for value in ("", None, "not a timestamp", 0):
			with self.subTest(value=value):
				self.assertIsNone(timezones.local(value, PACIFIC, PACIFIC))


class TheRequestedZoneIsHonouredOrRefused(V12TestCase):
	def setUp(self):
		super().setUp()
		_seed_zone(PACIFIC)

	def test_no_argument_means_the_sites_own_zone(self):
		self.assertEqual(timezones.resolve({}), PACIFIC)

	def test_a_named_zone_wins(self):
		self.assertEqual(timezones.resolve({"timezone": "America/Denver"}), "America/Denver")

	def test_an_unknown_zone_is_refused_rather_than_silently_ignored(self):
		"""A caller that asked for America/Los_Angeles and got UTC because of a
		typo has no way to tell from the numbers, and the numbers are what
		somebody schedules irrigation against."""
		with self.assertRaises(ToolError) as caught:
			timezones.resolve({"timezone": "America/Los_Angles"})
		self.assertIn("America/Los_Angles", str(caught.exception))
		self.assertIn("Area/Location", str(caught.exception))


class TheRendererIsTheResponseBlock(V12TestCase):
	def setUp(self):
		super().setUp()
		_seed_zone(PACIFIC)

	def test_the_block_says_both_zones(self):
		clock = timezones.Renderer({"timezone": "America/Denver"})
		block = clock.block()
		self.assertEqual(block["timezone"], "America/Denver")
		self.assertEqual(block["stored_timezone"], PACIFIC)
		self.assertIn("System Settings", block["timezone_source"])

	def test_add_writes_a_twin_for_each_named_field(self):
		clock = timezones.Renderer({})
		row = {"performed_at": "2026-07-24 06:00:00", "other": "x"}
		clock.add(row, "performed_at")
		self.assertEqual(row["performed_at"], "2026-07-24 06:00:00")
		self.assertEqual(row["performed_at_local"], "2026-07-24T06:00:00.000-07:00")

	def test_add_skips_a_field_that_is_not_in_the_row(self):
		"""A describer that omits a key on a record with no such event must not
		grow a null twin — a client testing `"closed_at" in row` keeps its answer."""
		clock = timezones.Renderer({})
		row = {"opened_at": "2026-07-24 06:00:00"}
		clock.add(row, "opened_at", "closed_at")
		self.assertIn("opened_at_local", row)
		self.assertNotIn("closed_at_local", row)

	def test_a_present_but_empty_field_gets_a_null_twin(self):
		clock = timezones.Renderer({})
		row = {"closed_at": None}
		clock.add(row, "closed_at")
		self.assertIsNone(row["closed_at_local"])


class TheStorageIsNotUTC(V12TestCase):
	"""The one that fails if somebody "standardises" the new columns on UTC.

	Frappe writes naive site-local datetimes and every timestamp in this app is
	one. Writing UTC into the new columns while the old ones keep site-local
	would put two zones in one table — an `open_valve` at 06:00 local and a
	`close_valve` at 13:00 UTC are the same seven o'clock, and
	`get_irrigation_runtime` pairing them reports an hour of irrigation that was
	sixty seconds.
	"""

	def test_a_stored_value_is_interpreted_in_the_site_zone(self):
		_seed_zone(PACIFIC)
		self.assertTrue(timezones.local("2026-07-24 06:00:00", "UTC", PACIFIC).startswith("2026-07-24T13:00"))

	def test_the_module_does_not_convert_anything_on_the_way_in(self):
		"""There is no `to_utc` here on purpose. `datetimes.as_mariadb_datetime`
		is the inbound boundary and it already has a documented rule; a second
		conversion in the other direction would be two answers to one question."""
		self.assertFalse(hasattr(timezones, "to_utc"))
		self.assertFalse(hasattr(timezones, "as_utc"))


class TheZoneDatabaseIsPresent(unittest.TestCase):
	def test_the_farms_own_zone_resolves_on_this_machine(self):
		"""zoneinfo falls back to the `tzdata` package where a system database is
		missing, and a server without either would answer every offset as UTC
		while reporting a Pacific zone name. Worth one assertion."""
		self.assertTrue(timezones.known(PACIFIC))
		offset = datetime.datetime(2026, 7, 24, tzinfo=zoneinfo.ZoneInfo(PACIFIC)).utcoffset()
		self.assertEqual(offset, datetime.timedelta(hours=-7))
