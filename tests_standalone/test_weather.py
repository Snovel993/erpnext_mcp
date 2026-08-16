# SPDX-License-Identifier: MIT
"""The weather timeline — v0.19.4's service, its five tools, and what a reading writes.

THE CLAIM BEHIND THE RELEASE is that the table v0.19.3 shipped empty is now the
half of a shift's evidence nobody could produce before. A foreman's logged water
break says what was done; the timeline says what it was done ABOUT, and OAR
437-004-1131 is a rule about conditions.

The refusals are what most of this module is for, because they are what a
scheduled job talking to somebody else's free API is made of. A 429 must not
become a retry storm, a timeout must not become a traceback, a duplicate must not
become a second row, and a temperature must not become a compliance record.

TEN CLAIMS.

 1. `TheServiceParsesWhatOpenMeteoReturns` — the two endpoints and the geocoder,
    normalised into the shape the child table stores.

 2. `TheHeatIndexIsComputedNotRead` — the number the rule turns on, from the
    observation, matching the worked example in the doctype's own description.

 3. `EveryFailureIsAMissingReading` — 429, 5xx, timeout, malformed JSON and a
    body with no `current` block all return None and none of them raises.

 4. `TheCacheAndTheBackoff` — two calls, one request; and a coordinate under
    backoff makes no request at all until it expires.

 5. `ResolvingAPlace` — coordinates parsed directly and never leaving the site, a
    place name geocoded once and cached, a transposed pair refused.

 6. `TheSnapshotOnAComplianceEvent` — the reading current at the event's own
    instant, earlier beating later, a manual figure never overwritten.

 7. `TheThresholdCrossing` — one event per shift and not one per reading, per
    company thresholds, wind only on a Spray shift.

 8. `TheHeatRecordComputesItsOwnMaxima` — from the timeline, and never over a
    number somebody typed.

 9. `TheBackfill` — idempotent by the minute, hourly, filtered to the shift's own
    period, and writing no compliance events.

10. `TheSweepAndTheTools` — the scheduled job over three open shifts with one of
    them rate-limited, and the five tools with their guards.
"""

import json
import types
import unittest

import frappe

from erpnext_mcp import shifts
from erpnext_mcp.services import weather

from .fixtures import MAIN, OTHER, V12TestCase, install_hrms
from .harness import ROLES, STORE, set_roles

ON = {
	f"allow_{name}": 1
	for name in (
		"start_shift",
		"add_worker_to_shift",
		"log_shift_event",
		"end_shift",
		"create_heat_exposure_event",
		"list_shifts",
		"get_shift",
		"fetch_weather_now",
		"backfill_weather_for_shift",
		"list_shifts_missing_weather",
		"get_weather_timeline",
		"get_weather_settings",
	)
}

FOREMAN = "HR-EMP-00001"  # Ada Orchard, Active, at MAIN
WORKER = "HR-EMP-00002"  # Ben Packhouse, Active, at MAIN
SIGNATURE = "/files/ada-shift-signature.png"

GPS = "45.52,-122.68"


def at(hour: int, minute: int = 0, day: str = "") -> str:
	day = day or frappe.utils.today()
	return f"{day} {hour:02d}:{minute:02d}:00"


# ── the fake far end ────────────────────────────────────────────────────────
class FakeResponse:
	"""Just enough of `requests.Response` for `_get_json` to make its decisions.

	`text` is real rather than a stub, because one of the branches under test is
	the 200 carrying an HTML error page — a captive portal on a farm office link
	is the ordinary version of that, and the message it logs quotes the body.
	"""

	def __init__(self, status_code=200, payload=None, body=None):
		self.status_code = status_code
		self._payload = payload
		self.text = body if body is not None else json.dumps(payload or {})

	def json(self):
		if self._payload is None:
			raise ValueError("Expecting value: line 1 column 1 (char 0)")
		return self._payload


class FakeOpenMeteo:
	"""A stand-in Open-Meteo, routed by which of the three hosts was called.

	It RECORDS EVERY CALL, which is what makes the cache assertions real: "two
	`fetch_current` calls produce one request" is only a claim about the cache if
	the second request would otherwise have been visible.
	"""

	def __init__(self):
		self.calls = []
		self.current = {}
		self.archive = {}
		self.geocode = {}
		self.raise_with = None

	# -- programming it ---------------------------------------------------
	def set_current(self, temp=72.0, humidity=40.0, wind=4.0, when=None, **extra):
		# DEFAULTS TO NOW rather than to a fixed hour, because `_due` measures a
		# reading's AGE against the wall clock — a fixture stamped 10:00 would be
		# hours stale by the afternoon and every "the sweep skips a shift it just
		# read" assertion would pass or fail depending on when the suite ran.
		self.current = {
			"current": {
				"time": when or frappe.utils.now()[:19],
				"temperature_2m": temp,
				"apparent_temperature": temp + 1,
				"relative_humidity_2m": humidity,
				"wind_speed_10m": wind,
				"wind_direction_10m": 210,
				"precipitation": 0.0,
				**extra,
			}
		}
		return self

	def set_archive(self, hours, temp=78.0, humidity=40.0, wind=3.0, start_hour=6, day=None):
		day = day or frappe.utils.today()
		self.archive = {
			"hourly": {
				"time": [f"{day}T{start_hour + index:02d}:00" for index in range(hours)],
				"temperature_2m": [temp] * hours,
				"apparent_temperature": [temp] * hours,
				"relative_humidity_2m": [humidity] * hours,
				"wind_speed_10m": [wind] * hours,
				"wind_direction_10m": [180] * hours,
				"precipitation": [0.0] * hours,
			}
		}
		return self

	def set_geocode(self, lat=45.9327, lon=-118.3877):
		self.geocode = {"results": [{"latitude": lat, "longitude": lon, "name": "Milton-Freewater"}]}
		return self

	# -- being called -----------------------------------------------------
	def get(self, url, params=None, timeout=None):
		self.calls.append({"url": url, "params": dict(params or {}), "timeout": timeout})
		if self.raise_with is not None:
			raise self.raise_with
		if "geocoding" in url:
			return self._answer(self.geocode)
		if "archive" in url:
			return self._answer(self.archive)
		return self._answer(self.current)

	def _answer(self, programmed):
		if isinstance(programmed, FakeResponse):
			return programmed
		return FakeResponse(200, programmed)

	def hits(self, kind: str) -> int:
		return len([call for call in self.calls if kind in call["url"]])


class WeatherTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ON)
		install_hrms()
		weather.reset_cache()
		self.addCleanup(weather.reset_cache)

		self.api = FakeOpenMeteo().set_current()
		self._install_requests(self.api)

		self._roles_before = {user: list(held) for user, held in ROLES.items()}
		self.addCleanup(self._restore_roles)

	def _restore_roles(self):
		ROLES.clear()
		ROLES.update(self._roles_before)

	def _install_requests(self, api):
		"""Put a `requests` module into `sys.modules` that this fake answers.

		`services/weather._get_json` imports `requests` INSIDE the function — so
		that a bench somehow missing it loses weather and nothing else — which
		means the import has to be satisfied at call time rather than patched at
		module scope. Installing a module object is the faithful way to do that:
		it exercises the real import statement rather than replacing the function
		that runs it.
		"""
		import sys

		module = types.ModuleType("requests")
		module.get = api.get
		previous = sys.modules.get("requests")
		sys.modules["requests"] = module

		def restore():
			if previous is None:
				sys.modules.pop("requests", None)
			else:
				sys.modules["requests"] = previous

		self.addCleanup(restore)

	# -- helpers -----------------------------------------------------------
	def start(self, **overrides):
		payload = {
			"foreman": FOREMAN,
			"location": "Block 7 North",
			"shift_type": "Harvest",
			"farm_location_gps": GPS,
			"start_datetime": at(6),
			"crew_employees": [WORKER],
		}
		payload.update(overrides)
		return self.tool_data("start_shift", payload)

	def close(self, shift: str, **overrides):
		payload = {
			"shift": shift,
			"end_datetime": at(15),
			"supervisor_signature_file_token": SIGNATURE,
		}
		payload.update(overrides)
		return self.tool_data("end_shift", payload)

	def raw(self, name: str) -> dict:
		return dict(STORE.get_raw(shifts.DOCTYPE, name) or {})

	def readings(self, name: str) -> list:
		return list(self.raw(name).get("weather_timeline") or [])

	def events(self, name: str, event_type=None) -> list:
		rows = list(self.raw(name).get("compliance_events") or [])
		if event_type:
			rows = [row for row in rows if row.get("event_type") == event_type]
		return rows

	def append(self, shift: str, *readings):
		"""Put readings on a shift directly, the way the service does."""
		return weather.append_readings(shift, list(readings))

	def reading(self, hour, temp=78.0, humidity=40.0, wind=3.0, minute=0, source=None):
		return {
			"reading_datetime": at(hour, minute),
			"temp_f": temp,
			"heat_index_f": weather.heat_index_f(temp, humidity),
			"humidity_pct": humidity,
			"wind_speed_mph": wind,
			"wind_direction_deg": 180,
			"precipitation_mm": 0.0,
			"source": source or weather.SOURCE_CURRENT,
			"fetched_at": frappe.utils.now(),
		}

	def override(self, company, **values):
		"""Write one per-company threshold row onto the Weather Settings single."""
		single = dict(STORE.singles.get(weather.SETTINGS_DOCTYPE) or {})
		single.setdefault("doctype", weather.SETTINGS_DOCTYPE)
		rows = list(single.get("per_company_overrides") or [])
		rows.append({"company": company, **values})
		single["per_company_overrides"] = rows
		STORE.singles[weather.SETTINGS_DOCTYPE] = single

	def weather_off(self):
		single = dict(STORE.singles.get(weather.SETTINGS_DOCTYPE) or {})
		single.setdefault("doctype", weather.SETTINGS_DOCTYPE)
		single["enabled"] = "0"
		STORE.singles[weather.SETTINGS_DOCTYPE] = single


# ── 1 ───────────────────────────────────────────────────────────────────────
class TheServiceParsesWhatOpenMeteoReturns(WeatherTestCase):
	def test_the_current_endpoint_becomes_the_shape_the_child_table_stores(self):
		self.api.set_current(temp=84.0, humidity=55.0, wind=7.5, when="2026-08-03T11:15")
		reading = weather.fetch_current(45.52, -122.68)
		# NORMALISED TO SECONDS. Open-Meteo answers ISO-8601 with a `T` and no
		# seconds; a Frappe Datetime column wants a space and wants them, and a
		# string one character short sorts wrong against every other reading.
		self.assertEqual(reading["reading_datetime"], "2026-08-03 11:15:00")
		self.assertEqual(reading["temp_f"], 84.0)
		self.assertEqual(reading["humidity_pct"], 55.0)
		self.assertEqual(reading["wind_speed_mph"], 7.5)
		self.assertEqual(reading["wind_direction_deg"], 210.0)
		self.assertEqual(reading["precipitation_mm"], 0.0)
		self.assertEqual(reading["source"], weather.SOURCE_CURRENT)
		self.assertTrue(reading["fetched_at"])

	def test_the_request_asks_for_fahrenheit_and_mph_but_not_inches(self):
		"""THE UNIT HAS TO MATCH THE COLUMN'S OWN LABEL. `precipitation_mm` says mm
		on the form, and a unit that disagrees with its label is the kind of error
		that survives for years and is found by somebody comparing against a
		gauge. Temperature and wind ARE converted, because those columns say °F
		and mph."""
		weather.fetch_current(45.52, -122.68)
		params = self.api.calls[-1]["params"]
		self.assertEqual(params["temperature_unit"], "fahrenheit")
		self.assertEqual(params["wind_speed_unit"], "mph")
		self.assertNotIn("precipitation_unit", params)

	def test_the_timeout_is_the_configured_one_and_is_never_absent(self):
		"""A request with no timeout holds a scheduler worker for as long as the
		far end feels like holding it."""
		weather.fetch_current(45.52, -122.68)
		self.assertEqual(self.api.calls[-1]["timeout"], weather.http_timeout_seconds())
		self.assertGreater(self.api.calls[-1]["timeout"], 0)

	def test_the_archive_endpoint_becomes_one_reading_per_hour(self):
		self.api.set_archive(hours=10, temp=91.0, humidity=35.0)
		rows = weather.fetch_archive(45.52, -122.68, at(6), at(16))
		self.assertEqual(len(rows), 10)
		self.assertEqual(rows[0]["reading_datetime"], at(6))
		self.assertEqual(rows[-1]["reading_datetime"], at(15))
		self.assertTrue(all(row["source"] == weather.SOURCE_ARCHIVE for row in rows))
		self.assertTrue(all(row["temp_f"] == 91.0 for row in rows))

	def test_the_archive_is_asked_for_whole_dates(self):
		self.api.set_archive(hours=2)
		weather.fetch_archive(45.52, -122.68, "2026-07-15 06:00:00", "2026-07-15 15:00:00")
		params = self.api.calls[-1]["params"]
		self.assertEqual(params["start_date"], "2026-07-15")
		self.assertEqual(params["end_date"], "2026-07-15")

	def test_a_series_shorter_than_the_time_axis_does_not_blow_up(self):
		"""Open-Meteo has returned a shorter array than its own `time` list. A
		normaliser that indexed blindly would raise inside a scheduled job."""
		self.api.set_archive(hours=4)
		self.api.archive["hourly"]["relative_humidity_2m"] = [40.0]
		rows = weather.fetch_archive(45.52, -122.68, at(6), at(10))
		self.assertEqual(len(rows), 4)
		self.assertIsNone(rows[3]["humidity_pct"])


# ── 2 ───────────────────────────────────────────────────────────────────────
class TheHeatIndexIsComputedNotRead(WeatherTestCase):
	def test_eighty_eight_at_seventy_percent_is_a_hundred(self):
		"""THE WORKED EXAMPLE IN THE DOCTYPE'S OWN DESCRIPTION. Open-Meteo's
		`apparent_temperature` is a different quantity — it folds in wind and
		radiation — and the rule turns on this one."""
		self.assertEqual(weather.heat_index_f(88.0, 70.0), 100.2)

	def test_a_cool_morning_stays_near_the_air_temperature(self):
		self.assertLess(weather.heat_index_f(62.0, 50.0), 70.0)

	def test_it_is_none_without_humidity_and_the_reading_falls_back(self):
		"""No humidity means no heat index, and `apparent_temperature` is the
		least bad stand-in — used only where the right number cannot be
		computed."""
		self.assertIsNone(weather.heat_index_f(88.0, None))
		self.api.set_current(temp=88.0, humidity=None)
		reading = weather.fetch_current(45.52, -122.68)
		self.assertEqual(reading["heat_index_f"], 89.0)

	def test_the_fetched_reading_carries_the_computed_index_and_not_the_apparent(self):
		self.api.set_current(temp=88.0, humidity=70.0)
		reading = weather.fetch_current(45.52, -122.68)
		self.assertEqual(reading["heat_index_f"], 100.2)
		self.assertNotEqual(reading["heat_index_f"], 89.0)

	def test_an_impossible_humidity_produces_no_index_rather_than_a_number(self):
		self.assertIsNone(weather.heat_index_f(88.0, 140.0))


# ── 3 ───────────────────────────────────────────────────────────────────────
class EveryFailureIsAMissingReading(WeatherTestCase):
	def test_a_429_returns_none_and_does_not_raise(self):
		self.api.current = FakeResponse(429, body="slow down")
		self.assertIsNone(weather.fetch_current(45.52, -122.68))

	def test_a_500_returns_none_and_does_not_raise(self):
		self.api.current = FakeResponse(503, body="upstream unavailable")
		self.assertIsNone(weather.fetch_current(45.52, -122.68))

	def test_a_timeout_returns_none(self):
		self.api.raise_with = TimeoutError("read timed out")
		self.assertIsNone(weather.fetch_current(45.52, -122.68))

	def test_malformed_json_returns_none(self):
		"""The 200 carrying an HTML login page. A captive portal on a farm office
		link is the ordinary version of this."""
		self.api.current = FakeResponse(200, payload=None, body="<html>sign in</html>")
		self.assertIsNone(weather.fetch_current(45.52, -122.68))

	def test_a_body_with_no_current_block_returns_none(self):
		self.api.current = {"latitude": 45.52, "longitude": -122.68}
		self.assertIsNone(weather.fetch_current(45.52, -122.68))

	def test_a_404_returns_none_without_starting_a_backoff(self):
		"""A 4xx that is not 429 is this app asking wrongly, not the far end
		asking us to stop. Backing off would delay the fix rather than the load."""
		self.api.current = FakeResponse(404, body="not found")
		self.assertIsNone(weather.fetch_current(45.52, -122.68))
		self.assertEqual(weather.backoff_seconds_remaining("45.52,-122.68"), 0)

	def test_a_non_http_endpoint_is_refused_before_any_client_is_reached(self):
		single = dict(STORE.singles.get(weather.SETTINGS_DOCTYPE) or {})
		single["open_meteo_base_url_current"] = "file:///etc/passwd"
		single["doctype"] = weather.SETTINGS_DOCTYPE
		STORE.singles[weather.SETTINGS_DOCTYPE] = single
		self.assertIsNone(weather.fetch_current(45.52, -122.68))
		self.assertEqual(self.api.calls, [])

	# -- the sweep's own contract -----------------------------------------
	def test_the_sweep_returns_zero_rather_than_raising_when_everything_fails(self):
		self.start()
		self.api.raise_with = RuntimeError("the network is gone")
		self.assertEqual(weather.sweep_open_shifts(), 0)


# ── 4 ───────────────────────────────────────────────────────────────────────
class TheCacheAndTheBackoff(WeatherTestCase):
	def test_two_calls_for_one_place_make_one_request(self):
		weather.fetch_current(45.52, -122.68)
		weather.fetch_current(45.52, -122.68)
		self.assertEqual(self.api.hits("forecast"), 1)

	def test_the_cache_key_rounds_to_about_eleven_metres(self):
		"""Two shifts on adjacent rows of one orchard share a request, which is
		the whole point — Open-Meteo models a grid square measured in kilometres,
		so asking twice about two points eleven metres apart returns the same
		number twice."""
		weather.fetch_current(45.520001, -122.680001)
		weather.fetch_current(45.520002, -122.680002)
		self.assertEqual(self.api.hits("forecast"), 1)

	def test_an_expired_entry_asks_again(self):
		weather.fetch_current(45.52, -122.68)
		self._age(weather.cache_ttl_seconds() + 1)
		weather.fetch_current(45.52, -122.68)
		self.assertEqual(self.api.hits("forecast"), 2)

	def test_a_429_stops_the_next_call_making_a_request_at_all(self):
		"""A rate limit answered by retrying immediately is how a site gets
		blocked rather than throttled."""
		self.api.current = FakeResponse(429, body="slow down")
		self.assertIsNone(weather.fetch_current(45.52, -122.68))
		self.assertEqual(self.api.hits("forecast"), 1)
		self.api.set_current(temp=70.0)
		self.assertIsNone(weather.fetch_current(45.52, -122.68))
		self.assertEqual(self.api.hits("forecast"), 1, "a second request went out under backoff")
		self.assertGreater(weather.backoff_seconds_remaining("45.52,-122.68"), 0)

	def test_the_backoff_expires_and_the_next_call_succeeds(self):
		self.api.current = FakeResponse(429, body="slow down")
		weather.fetch_current(45.52, -122.68)
		self._age(weather.BACKOFF_START_SECONDS + 1)
		self.api.set_current(temp=70.0)
		reading = weather.fetch_current(45.52, -122.68)
		self.assertIsNotNone(reading)
		self.assertEqual(reading["temp_f"], 70.0)

	def test_the_backoff_doubles_and_is_capped_at_an_hour(self):
		scope = "45.52,-122.68"
		waits = [weather._start_backoff(scope) for _ in range(12)]
		self.assertEqual(waits[0], weather.BACKOFF_START_SECONDS)
		self.assertEqual(waits[1], weather.BACKOFF_START_SECONDS * 2)
		self.assertEqual(waits[-1], weather.BACKOFF_CAP_SECONDS)
		self.assertTrue(all(wait <= weather.BACKOFF_CAP_SECONDS for wait in waits))

	def test_the_backoff_is_per_coordinate(self):
		"""One unlucky block must not stop the sweep documenting every other."""
		self.api.current = FakeResponse(429, body="slow down")
		weather.fetch_current(45.52, -122.68)
		self.assertGreater(weather.backoff_seconds_remaining("45.52,-122.68"), 0)
		self.assertEqual(weather.backoff_seconds_remaining("46.10,-119.00"), 0)

	def test_a_success_clears_a_backoff_rather_than_serving_it_out(self):
		self.api.current = FakeResponse(429, body="slow down")
		weather.fetch_current(45.52, -122.68)
		self._age(weather.BACKOFF_START_SECONDS + 1)
		self.api.set_current(temp=70.0)
		weather.fetch_current(45.52, -122.68)
		self.assertEqual(weather.backoff_seconds_remaining("45.52,-122.68"), 0)

	# -- plumbing ---------------------------------------------------------
	def _age(self, seconds):
		"""Move the service's clock forward without sleeping."""
		base = weather._clock()
		weather._clock = lambda: base + seconds
		self.addCleanup(self._restore_clock)

	def _restore_clock(self):
		import time

		weather._clock = lambda: time.time()


# ── 5 ───────────────────────────────────────────────────────────────────────
class ResolvingAPlace(WeatherTestCase):
	def test_a_coordinate_pair_is_parsed_here_and_never_leaves_the_site(self):
		self.assertEqual(weather.resolve_location("45.52,-122.68"), (45.52, -122.68))
		self.assertEqual(self.api.calls, [])

	def test_the_spelling_the_doctype_documents_is_accepted(self):
		self.assertEqual(weather.resolve_location("Latitude 45.52, longitude -122.68"), (45.52, -122.68))
		self.assertEqual(self.api.calls, [])

	def test_a_place_name_costs_one_geocoding_call_and_is_then_cached(self):
		self.api.set_geocode(lat=45.9327, lon=-118.3877)
		first = weather.resolve_location("Milton-Freewater, OR")
		second = weather.resolve_location("Milton-Freewater, OR")
		self.assertEqual(first, (45.9327, -118.3877))
		self.assertEqual(second, first)
		self.assertEqual(self.api.hits("geocoding"), 1)

	def test_the_geocoder_is_asked_for_exactly_one_match(self):
		self.api.set_geocode()
		weather.resolve_location("Milton-Freewater, OR")
		self.assertEqual(self.api.calls[-1]["params"]["count"], 1)

	def test_a_name_the_geocoder_does_not_know_is_cached_as_a_negative(self):
		"""A name that is unknown will not become known inside the cache window,
		and asking again every tick is how a free service stops answering."""
		self.api.geocode = {"results": []}
		self.assertIsNone(weather.resolve_location("Somewhere Nobody Named"))
		self.assertIsNone(weather.resolve_location("Somewhere Nobody Named"))
		self.assertEqual(self.api.hits("geocoding"), 1)

	def test_a_transposed_pair_is_refused_rather_than_clamped(self):
		"""-122.68,45.52 is a real place in the Yellow Sea. Fetching weather for
		it and filing the answer against an Oregon shift would be a fabricated
		record that looks exactly like a true one."""
		self.assertIsNone(weather.parse_coordinates("-122.68,45.52"))

	def test_an_empty_location_resolves_to_nothing(self):
		self.assertIsNone(weather.resolve_location(""))
		self.assertIsNone(weather.resolve_location("   "))


# ── 6 ───────────────────────────────────────────────────────────────────────
class TheSnapshotOnAComplianceEvent(WeatherTestCase):
	def test_an_event_takes_the_reading_current_at_its_own_instant(self):
		data = self.start()
		self.append(data["name"], self.reading(10, temp=86.0, humidity=45.0))
		self.tool_data(
			"log_shift_event",
			{"shift": data["name"], "event_type": "Water Break", "event_datetime": at(10, 15)},
		)
		row = self.events(data["name"], "Water Break")[0]
		self.assertEqual(row["weather_snapshot_temp_f"], 86.0)
		self.assertEqual(row["weather_snapshot_heat_index_f"], weather.heat_index_f(86.0, 45.0))

	def test_earlier_beats_later(self):
		"""The reading that was current at 10:15 is the one taken at 10:00 — that
		is the conditions the foreman was standing in when they called the
		break — not the one taken at 10:20."""
		data = self.start()
		self.append(
			data["name"],
			self.reading(10, temp=86.0),
			self.reading(10, minute=20, temp=94.0),
		)
		self.tool_data(
			"log_shift_event",
			{"shift": data["name"], "event_type": "Water Break", "event_datetime": at(10, 15)},
		)
		self.assertEqual(self.events(data["name"], "Water Break")[0]["weather_snapshot_temp_f"], 86.0)

	def test_a_later_reading_is_used_when_there_is_nothing_before(self):
		"""The ordinary case for an event logged in the first minutes of a shift,
		and still the closest true statement available."""
		data = self.start()
		self.append(data["name"], self.reading(6, minute=10, temp=71.0))
		self.tool_data(
			"log_shift_event",
			{"shift": data["name"], "event_type": "Supervisor Observation", "event_datetime": at(6)},
		)
		row = self.events(data["name"], "Supervisor Observation")[0]
		self.assertEqual(row["weather_snapshot_temp_f"], 71.0)

	def test_nothing_is_copied_from_more_than_half_an_hour_away(self):
		"""A temperature from an hour away is not evidence about this moment, and
		a blank column is honest where a stale one is not."""
		data = self.start()
		self.append(data["name"], self.reading(8, temp=70.0))
		self.tool_data(
			"log_shift_event",
			{"shift": data["name"], "event_type": "Water Break", "event_datetime": at(12)},
		)
		row = self.events(data["name"], "Water Break")[0]
		self.assertIn(row.get("weather_snapshot_temp_f"), (None, ""))

	def test_a_manual_figure_is_never_overwritten(self):
		"""A foreman with a thermometer at the block knows something Open-Meteo's
		grid square does not, and the manual figure is the better evidence."""
		data = self.start()
		self.append(data["name"], self.reading(10, temp=86.0))
		self.tool_data(
			"log_shift_event",
			{
				"shift": data["name"],
				"event_type": "Water Break",
				"event_datetime": at(10, 15),
				"weather_snapshot_temp_f": 103.0,
			},
		)
		self.assertEqual(self.events(data["name"], "Water Break")[0]["weather_snapshot_temp_f"], 103.0)

	def test_a_shift_with_no_timeline_leaves_the_snapshot_alone(self):
		data = self.start()
		self.tool_data(
			"log_shift_event",
			{"shift": data["name"], "event_type": "Water Break", "event_datetime": at(10)},
		)
		row = self.events(data["name"], "Water Break")[0]
		self.assertIn(row.get("weather_snapshot_temp_f"), (None, ""))


# ── 7 ───────────────────────────────────────────────────────────────────────
class TheThresholdCrossing(WeatherTestCase):
	def test_a_reading_over_the_threshold_logs_one_event(self):
		self.api.set_current(temp=82.0, humidity=30.0)
		data = self.start()
		self.tool_data("fetch_weather_now", {"shift": data["name"]})
		rows = self.events(data["name"], weather.THRESHOLD_EVENT)
		self.assertEqual(len(rows), 1)
		self.assertIn("82.0", rows[0]["description"])

	def test_a_second_crossing_does_not_log_a_second_event(self):
		"""ONE PER SHIFT, NOT ONE PER READING. A nine-hour afternoon above eighty
		would otherwise bury the water breaks under thirty-six identical rows."""
		self.api.set_current(temp=82.0, humidity=30.0, when=at(10))
		data = self.start()
		self.tool_data("fetch_weather_now", {"shift": data["name"]})
		self.api.set_current(temp=84.0, humidity=30.0, when=at(10, 30))
		self.tool_data("fetch_weather_now", {"shift": data["name"]})
		self.assertEqual(len(self.readings(data["name"])), 2)
		self.assertEqual(len(self.events(data["name"], weather.THRESHOLD_EVENT)), 1)

	def test_a_reading_below_the_threshold_logs_nothing(self):
		self.api.set_current(temp=71.0, humidity=30.0)
		data = self.start()
		self.tool_data("fetch_weather_now", {"shift": data["name"]})
		self.assertEqual(self.events(data["name"], weather.THRESHOLD_EVENT), [])

	def test_the_event_names_nobody_as_having_logged_it(self):
		"""`logged_by` is a Link to Employee and nobody logged this — the sweep
		did. Naming the foreman would put their identity against an observation
		they did not make, on the one doctype whose value is saying who did what."""
		self.api.set_current(temp=82.0, humidity=30.0)
		data = self.start()
		self.tool_data("fetch_weather_now", {"shift": data["name"]})
		row = self.events(data["name"], weather.THRESHOLD_EVENT)[0]
		self.assertIn(row.get("logged_by"), (None, ""))

	def test_no_heat_exposure_event_is_ever_created_by_a_reading(self):
		"""THE LINE THIS RELEASE DRAWS. That record names which crew was exposed,
		what water was provided and whether anybody showed signs, and it carries a
		signature — five judgements by the person who was standing there."""
		self.api.set_current(temp=99.0, humidity=60.0)
		data = self.start()
		self.tool_data("fetch_weather_now", {"shift": data["name"]})
		self.assertEqual(STORE.rows(shifts.HEAT_DOCTYPE), [])

	def test_a_company_override_lowers_the_threshold_for_that_entity_only(self):
		self.override(MAIN, heat_threshold_temp_f=75.0)
		limits = weather.thresholds_for(MAIN)
		self.assertEqual(limits["heat_threshold_temp_f"], 75.0)
		self.assertIn("heat_threshold_temp_f", limits["overridden"])
		self.assertEqual(weather.thresholds_for(OTHER)["heat_threshold_temp_f"], 80.0)

	def test_the_override_fires_an_event_the_default_would_not(self):
		self.override(MAIN, heat_threshold_temp_f=75.0)
		self.api.set_current(temp=76.0, humidity=20.0)
		data = self.start()
		self.tool_data("fetch_weather_now", {"shift": data["name"]})
		self.assertEqual(len(self.events(data["name"], weather.THRESHOLD_EVENT)), 1)

	def test_the_same_reading_on_the_default_threshold_fires_nothing(self):
		self.api.set_current(temp=76.0, humidity=20.0)
		data = self.start()
		self.tool_data("fetch_weather_now", {"shift": data["name"]})
		self.assertEqual(self.events(data["name"], weather.THRESHOLD_EVENT), [])

	def test_a_blank_column_on_an_override_falls_back_and_is_not_read_as_zero(self):
		"""A row that exists to lower one entity's wind limit must leave its heat
		limits alone — a zero heat threshold would make every reading a crossing,
		which is the same as making none of them mean anything."""
		self.override(MAIN, wind_threshold_mph_spray_block=8.0)
		limits = weather.thresholds_for(MAIN)
		self.assertEqual(limits["wind_threshold_mph_spray_block"], 8.0)
		self.assertEqual(limits["heat_threshold_temp_f"], 80.0)
		self.assertEqual(limits["heat_threshold_heat_index_f"], 80.0)

	def test_wind_fires_on_a_spray_shift(self):
		self.api.set_current(temp=64.0, humidity=50.0, wind=17.0)
		data = self.start(shift_type="Spray")
		self.tool_data("fetch_weather_now", {"shift": data["name"]})
		rows = self.events(data["name"], weather.THRESHOLD_EVENT)
		self.assertEqual(len(rows), 1)
		self.assertIn("spray-block", rows[0]["description"])

	def test_the_same_wind_on_a_harvest_shift_fires_nothing(self):
		"""Fifteen miles an hour during a prune is weather; during an application
		it is a decision somebody has to make."""
		self.api.set_current(temp=64.0, humidity=50.0, wind=17.0)
		data = self.start(shift_type="Harvest")
		self.tool_data("fetch_weather_now", {"shift": data["name"]})
		self.assertEqual(self.events(data["name"], weather.THRESHOLD_EVENT), [])


# ── 8 ───────────────────────────────────────────────────────────────────────
class TheHeatRecordComputesItsOwnMaxima(WeatherTestCase):
	def _heat(self, shift, **overrides):
		payload = {
			"farm_shift": shift,
			"supervisor_signature_file_token": SIGNATURE,
			"water_provided": True,
			"shade_provided": True,
			"mandatory_rest_taken": True,
			"training_verified": False,
		}
		payload.update(overrides)
		return self.tool_data("create_heat_exposure_event", payload)

	def test_the_maxima_come_off_the_timeline(self):
		data = self.start()
		for hour, temp in ((8, 78.0), (10, 82.0), (12, 88.0), (13, 85.0), (14, 80.0)):
			self.append(data["name"], self.reading(hour, temp=temp, humidity=30.0))
		self.close(data["name"])
		record = self._heat(data["name"])
		self.assertEqual(record["max_temp_f"], 88.0)
		self.assertEqual(record["max_heat_index_f"], weather.heat_index_f(88.0, 30.0))

	def test_the_threshold_time_is_the_earliest_crossing_not_the_hottest_moment(self):
		"""Every obligation -1131 adds runs from the instant the shift PASSED the
		threshold; the peak is what max_heat_index_f is for."""
		data = self.start()
		for hour, temp in ((8, 78.0), (10, 82.0), (12, 88.0)):
			self.append(data["name"], self.reading(hour, temp=temp, humidity=30.0))
		self.close(data["name"])
		record = self._heat(data["name"])
		self.assertEqual(str(record["threshold_crossed_at"]), at(10))

	def test_a_manual_maximum_is_never_recomputed(self):
		"""An on-site reading beats a modelled figure for a grid square measured
		in kilometres, and the foreman wrote it on a record they are signing."""
		data = self.start()
		for hour, temp in ((8, 78.0), (12, 88.0)):
			self.append(data["name"], self.reading(hour, temp=temp, humidity=30.0))
		self.close(data["name"])
		record = self._heat(data["name"], max_temp_f=90.0)
		self.assertEqual(record["max_temp_f"], 90.0)

	def test_a_shift_with_no_timeline_leaves_the_maxima_empty(self):
		data = self.start()
		self.close(data["name"])
		record = self._heat(data["name"])
		self.assertIn(record.get("max_temp_f"), (None, ""))
		self.assertIn(record.get("threshold_crossed_at"), (None, ""))

	def test_a_timeline_that_never_crosses_leaves_the_threshold_time_empty(self):
		data = self.start()
		for hour in (8, 10, 12):
			self.append(data["name"], self.reading(hour, temp=68.0, humidity=30.0))
		self.close(data["name"])
		record = self._heat(data["name"])
		self.assertEqual(record["max_temp_f"], 68.0)
		self.assertIn(record.get("threshold_crossed_at"), (None, ""))

	def test_a_company_override_moves_the_crossing_earlier(self):
		self.override(MAIN, heat_threshold_temp_f=70.0)
		data = self.start()
		for hour, temp in ((8, 72.0), (12, 88.0)):
			self.append(data["name"], self.reading(hour, temp=temp, humidity=30.0))
		self.close(data["name"])
		self.assertEqual(str(self._heat(data["name"])["threshold_crossed_at"]), at(8))


# ── 9 ───────────────────────────────────────────────────────────────────────
class TheBackfill(WeatherTestCase):
	def _closed(self):
		data = self.start()
		self.close(data["name"])
		return data["name"]

	def test_ten_hours_of_archive_become_ten_readings(self):
		shift = self._closed()
		self.api.set_archive(hours=10, temp=79.0, start_hour=6)
		report = self.tool_data("backfill_weather_for_shift", {"shift": shift})
		self.assertEqual(report["added"], 10)
		self.assertEqual(report["skipped_as_duplicate"], 0)
		self.assertEqual(report["failed"], 0)
		self.assertEqual(len(self.readings(shift)), 10)

	def test_a_second_run_adds_nothing_and_reports_what_it_skipped(self):
		shift = self._closed()
		self.api.set_archive(hours=10, temp=79.0, start_hour=6)
		self.tool_data("backfill_weather_for_shift", {"shift": shift})
		again = self.tool_data("backfill_weather_for_shift", {"shift": shift})
		self.assertEqual(again["added"], 0)
		self.assertEqual(again["skipped_as_duplicate"], 10)
		self.assertEqual(len(self.readings(shift)), 10)

	def test_every_backfilled_row_says_it_came_from_the_archive(self):
		shift = self._closed()
		self.api.set_archive(hours=4, start_hour=7)
		self.tool_data("backfill_weather_for_shift", {"shift": shift})
		self.assertTrue(all(row["source"] == weather.SOURCE_ARCHIVE for row in self.readings(shift)))

	def test_readings_outside_the_shifts_own_period_are_dropped(self):
		"""The archive answers by whole days. A six-hour morning shift must not
		acquire a timeline that runs to midnight — that would be a defensible
		weather record and a false statement about an exposure period."""
		shift = self._closed()  # 06:00 → 15:00
		self.api.set_archive(hours=24, start_hour=0)
		report = self.tool_data("backfill_weather_for_shift", {"shift": shift})
		self.assertEqual(report["added"], 10)
		self.assertEqual(report["outside_the_shift_period"], 14)

	def test_it_keeps_live_readings_and_fills_the_gaps_between_them(self):
		data = self.start()
		self.append(data["name"], self.reading(9, minute=0, temp=88.0, source=weather.SOURCE_CURRENT))
		self.close(data["name"])
		self.api.set_archive(hours=10, temp=79.0, start_hour=6)
		report = self.tool_data("backfill_weather_for_shift", {"shift": data["name"]})
		self.assertEqual(report["added"], 9)
		self.assertEqual(report["skipped_as_duplicate"], 1)
		live = [row for row in self.readings(data["name"]) if row["source"] == weather.SOURCE_CURRENT]
		self.assertEqual(len(live), 1)
		self.assertEqual(live[0]["temp_f"], 88.0)

	def test_it_writes_no_compliance_events_however_hot_the_archive_was(self):
		"""A Threshold Crossed row dated last July on a closed and signed shift
		would be an observation nobody made, sitting beside water breaks somebody
		did. The crossings are counted and reported instead."""
		shift = self._closed()
		self.api.set_archive(hours=10, temp=99.0, humidity=50.0, start_hour=6)
		report = self.tool_data("backfill_weather_for_shift", {"shift": shift})
		self.assertEqual(self.events(shift, weather.THRESHOLD_EVENT), [])
		self.assertEqual(report["readings_at_or_above_the_heat_threshold"], 10)
		self.assertIn("NO COMPLIANCE EVENT WAS WRITTEN", report["threshold_note"])

	def test_an_open_shift_is_refused_and_told_what_to_use(self):
		data = self.start()
		message = self.tool_error("backfill_weather_for_shift", {"shift": data["name"]})
		self.assertIn("still open", message)
		self.assertIn("fetch_weather_now", message)

	def test_a_shift_with_no_coordinates_is_refused(self):
		data = self.start(farm_location_gps="")
		self.close(data["name"])
		message = self.tool_error("backfill_weather_for_shift", {"shift": data["name"]})
		self.assertIn("farm_location_gps", message)

	def test_an_archive_that_answers_nothing_is_refused_without_writing(self):
		shift = self._closed()
		self.api.archive = FakeResponse(503, body="down")
		message = self.tool_error("backfill_weather_for_shift", {"shift": shift})
		self.assertIn("archive", message.lower())
		self.assertEqual(self.readings(shift), [])


# ── 10 ──────────────────────────────────────────────────────────────────────
class TheSweepAndTheTools(WeatherTestCase):
	def test_a_site_with_no_open_shifts_sweeps_cleanly(self):
		self.assertEqual(weather.sweep_open_shifts(), 0)
		self.assertEqual(self.api.calls, [])

	def test_three_open_shifts_get_three_readings(self):
		# ONLY THE FIRST CARRIES THE CREW, because nobody is on two open shifts at
		# once — see `test_shifts.NobodyIsOnTwoOpenShiftsAtOnce`. The sweep reads
		# each shift's own coordinates and never looks at its crew, so a roster
		# here would only be a way of tripping a guard this test is not about.
		names = [
			self.start(
				location=f"Block {index}",
				farm_location_gps=gps,
				crew_employees=[WORKER] if index == 1 else [],
			)["name"]
			for index, gps in enumerate(("45.52,-122.68", "46.10,-119.00", "44.05,-121.30"), start=1)
		]
		self.assertEqual(weather.sweep_open_shifts(), 3)
		for name in names:
			self.assertEqual(len(self.readings(name)), 1)

	def test_one_rate_limited_place_does_not_stop_the_other_two(self):
		"""A block whose coordinates the far end refuses is one block's gap, not
		a day nobody's crew is documented."""
		blocked = self.start(location="Block 1", farm_location_gps="45.52,-122.68")["name"]
		# Crewless for the reason above: the other two blocks are here for their
		# coordinates, and the same picker cannot stand on all three at once.
		others = [
			self.start(location="Block 2", farm_location_gps="46.10,-119.00", crew_employees=[])["name"],
			self.start(location="Block 3", farm_location_gps="44.05,-121.30", crew_employees=[])["name"],
		]
		api = self.api

		def selective(url, params=None, timeout=None):
			if str(params.get("latitude")) == "45.52":
				api.calls.append({"url": url, "params": dict(params), "timeout": timeout})
				return FakeResponse(429, body="slow down")
			return api.get(url, params=params, timeout=timeout)

		import sys

		sys.modules["requests"].get = selective
		self.assertEqual(weather.sweep_open_shifts(), 2)
		self.assertEqual(self.readings(blocked), [])
		for name in others:
			self.assertEqual(len(self.readings(name)), 1)
		self.assertGreater(weather.backoff_seconds_remaining("45.52,-122.68"), 0)

	def test_a_shift_with_no_coordinates_is_not_swept_at_all(self):
		name = self.start(farm_location_gps="")["name"]
		self.assertEqual(weather.sweep_open_shifts(), 0)
		self.assertEqual(self.readings(name), [])
		self.assertEqual(self.api.calls, [])

	def test_a_closed_shift_is_not_swept(self):
		name = self.start()["name"]
		self.close(name)
		self.assertEqual(weather.sweep_open_shifts(), 0)

	def test_a_shift_read_within_the_interval_is_skipped(self):
		"""THE CRON IS THE CEILING AND fetch_interval_minutes IS THE FLOOR. A
		Frappe cron expression cannot be rewritten from a form, so the setting is
		honoured by skipping a shift whose newest reading is younger than it."""
		name = self.start()["name"]
		self.assertEqual(weather.sweep_open_shifts(), 1)
		weather.reset_cache()
		self.api.set_current(temp=73.0)
		self.assertEqual(weather.sweep_open_shifts(), 0)
		self.assertEqual(len(self.readings(name)), 1)

	def test_the_kill_switch_stops_the_sweep_before_any_query(self):
		self.start()
		self.weather_off()
		self.assertEqual(weather.sweep_open_shifts(), 0)
		self.assertEqual(self.api.calls, [])

	# -- the tools ---------------------------------------------------------
	def test_fetch_weather_now_refuses_a_closed_shift(self):
		name = self.start()["name"]
		self.close(name)
		message = self.tool_error("fetch_weather_now", {"shift": name})
		self.assertIn("backfill_weather_for_shift", message)

	def test_fetch_weather_now_bypasses_the_cache(self):
		name = self.start()["name"]
		self.tool_data("fetch_weather_now", {"shift": name})
		self.api.set_current(temp=91.0, humidity=40.0, when=at(23, 59))
		data = self.tool_data("fetch_weather_now", {"shift": name})
		self.assertEqual(self.api.hits("forecast"), 2)
		self.assertEqual(data["reading"]["temp_f"], 91.0)

	def test_fetch_weather_now_refuses_when_weather_is_switched_off(self):
		name = self.start()["name"]
		self.weather_off()
		message = self.tool_error("fetch_weather_now", {"shift": name})
		self.assertIn("Weather Settings", message)
		self.assertEqual(self.api.calls, [])

	def test_the_mutating_tools_are_off_by_default(self):
		from erpnext_mcp import registry

		for name in ("fetch_weather_now", "backfill_weather_for_shift"):
			with self.subTest(tool=name):
				self.assertTrue(registry.TOOLS[name]["mutating"])
				self.assertNotIn(name, registry.DEFAULT_ON_MUTATING_TOOLS)

	def test_a_disabled_tool_names_the_switch_to_tick(self):
		self.configure(enabled=1, **{**ON, "allow_fetch_weather_now": 0})
		name = self.start()["name"]
		message = self.tool_error("fetch_weather_now", {"shift": name})
		self.assertIn("allow_fetch_weather_now", message)

	def test_a_principal_without_an_hr_role_is_refused(self):
		name = self.start()["name"]
		set_roles("Administrator", ["Accounts Manager"])
		message = self.tool_error("fetch_weather_now", {"shift": name})
		self.assertIn("personnel", message)

	def test_get_weather_timeline_reports_the_first_crossing(self):
		name = self.start()["name"]
		for hour, temp in ((8, 74.0), (10, 84.0), (12, 90.0)):
			self.append(name, self.reading(hour, temp=temp, humidity=30.0))
		data = self.tool_data("get_weather_timeline", {"shift": name})
		self.assertEqual(data["count"], 3)
		self.assertEqual(data["first_crossing"], at(10))
		self.assertEqual(data["extremes"]["max_temp_f"], 90.0)
		self.assertEqual(data["readings_at_or_above_the_heat_threshold"], 2)

	def test_get_weather_timeline_windows_by_datetime(self):
		name = self.start()["name"]
		for hour in (8, 10, 12, 14):
			self.append(name, self.reading(hour, temp=75.0))
		data = self.tool_data(
			"get_weather_timeline",
			{"shift": name, "from_datetime": at(10), "to_datetime": at(12)},
		)
		self.assertEqual(data["count"], 2)

	def test_get_weather_timeline_calls_out_a_mixed_timeline(self):
		name = self.start()["name"]
		self.append(
			name,
			self.reading(8, source=weather.SOURCE_CURRENT),
			self.reading(9, source=weather.SOURCE_ARCHIVE),
		)
		data = self.tool_data("get_weather_timeline", {"shift": name})
		self.assertIn("MIXED", data["source_note"])

	def test_get_weather_timeline_says_what_to_check_when_it_is_empty(self):
		name = self.start()["name"]
		data = self.tool_data("get_weather_timeline", {"shift": name})
		self.assertEqual(data["count"], 0)
		self.assertIn("scheduler", data["note"])

	def test_list_shifts_missing_weather_finds_the_thin_one(self):
		"""Three readings across eight hours is thin; eight is not."""
		thin = self.start(location="Block A", start_datetime=at(6))["name"]
		self.append(thin, *[self.reading(hour) for hour in (6, 7, 8)])
		self.close(thin, end_datetime=at(14))

		full = self.start(location="Block B", start_datetime=at(6))["name"]
		self.append(full, *[self.reading(hour) for hour in range(6, 14)])
		self.close(full, end_datetime=at(14))

		data = self.tool_data("list_shifts_missing_weather", {"company": MAIN})
		found = [row["name"] for row in data["shifts"]]
		self.assertIn(thin, found)
		self.assertNotIn(full, found)
		self.assertEqual(data["already_documented"], 1)

	def test_list_shifts_missing_weather_separates_the_ones_with_no_place(self):
		nowhere = self.start(location="Block C", farm_location_gps="")["name"]
		self.close(nowhere)
		data = self.tool_data("list_shifts_missing_weather", {"company": MAIN})
		self.assertEqual([row["name"] for row in data["without_coordinates"]], [nowhere])
		self.assertNotIn(nowhere, [row["name"] for row in data["shifts"]])
		self.assertIn("different action", data["coordinates_note"])

	def test_list_shifts_missing_weather_ignores_open_shifts(self):
		open_shift = self.start()["name"]
		data = self.tool_data("list_shifts_missing_weather", {"company": MAIN})
		self.assertNotIn(open_shift, [row["name"] for row in data["shifts"]])

	def test_get_weather_settings_reports_the_thresholds_and_the_overrides(self):
		self.override(MAIN, heat_threshold_temp_f=75.0)
		data = self.tool_data("get_weather_settings", {})
		self.assertTrue(data["enabled"])
		self.assertEqual(data["defaults"]["heat_threshold_temp_f"], 80.0)
		self.assertEqual(data["per_company_overrides"][0]["company"], MAIN)
		self.assertEqual(data["per_company_overrides"][0]["heat_threshold_temp_f"], 75.0)
		self.assertEqual(data["schedule"], "*/15 * * * *")

	def test_get_weather_settings_still_answers_when_weather_is_off(self):
		"""It is the tool somebody calls to find out WHY nothing is being fetched,
		and a read that refused because the thing it reports is off would refuse
		in exactly the moment it is useful."""
		self.weather_off()
		data = self.tool_data("get_weather_settings", {})
		self.assertFalse(data["enabled"])
		self.assertIn("SWITCHED OFF", data["note"])

	def test_there_is_no_tool_that_writes_the_settings(self):
		"""A model that could raise the heat threshold past anything Oregon
		produces would leave a site that behaves normally and never says anything
		is wrong."""
		from erpnext_mcp import registry

		self.assertNotIn("update_weather_settings", registry.TOOLS)
		self.assertNotIn("set_weather_settings", registry.TOOLS)

	def test_get_weather_settings_reports_the_open_shifts_being_swept(self):
		name = self.start()["name"]
		data = self.tool_data("get_weather_settings", {})
		self.assertIn(name, data["open_shifts_with_coordinates"])


# ── the settings form's own refusals ────────────────────────────────────────
class TheSettingsFormRefusesWhatWouldFailSilently(WeatherTestCase):
	def doc(self, **values):
		payload = {"doctype": weather.SETTINGS_DOCTYPE, "name": weather.SETTINGS_DOCTYPE}
		for field in frappe.get_meta(weather.SETTINGS_DOCTYPE).fields:
			if field.get("default") not in (None, ""):
				payload[field["fieldname"]] = field["default"]
		payload.update(values)
		return frappe.get_doc(payload)

	def test_a_zero_timeout_is_refused(self):
		"""A timeout of zero is not 'no timeout' to `requests`; it is a connection
		that fails immediately, every time, silently."""
		with self.assertRaises(Exception) as caught:
			self.doc(http_timeout_seconds=0).save()
		self.assertIn("HTTP Timeout", str(caught.exception))

	def test_a_zero_cache_lifetime_is_refused(self):
		with self.assertRaises(Exception) as caught:
			self.doc(cache_ttl_seconds=0).save()
		self.assertIn("Cache TTL", str(caught.exception))

	def test_a_url_that_is_not_http_is_refused_on_the_form(self):
		with self.assertRaises(Exception) as caught:
			self.doc(open_meteo_base_url_current="file:///etc/passwd").save()
		self.assertIn("http", str(caught.exception).lower())

	def test_a_negative_threshold_is_refused(self):
		"""A minus key struck by accident makes EVERY reading a crossing, which is
		the same as making none of them mean anything."""
		with self.assertRaises(Exception) as caught:
			self.doc(heat_threshold_temp_f=-80).save()
		self.assertIn("negative threshold makes EVERY reading a crossing", str(caught.exception))

	def test_two_override_rows_for_one_company_are_refused(self):
		doc = self.doc()
		doc.append("per_company_overrides", {"company": MAIN, "heat_threshold_temp_f": 75.0})
		doc.append("per_company_overrides", {"company": MAIN, "heat_threshold_temp_f": 78.0})
		with self.assertRaises(Exception) as caught:
			doc.save()
		self.assertIn("two override rows", str(caught.exception))

	def test_one_row_per_company_saves(self):
		doc = self.doc()
		doc.append("per_company_overrides", {"company": MAIN, "heat_threshold_temp_f": 75.0})
		doc.append("per_company_overrides", {"company": OTHER, "heat_threshold_temp_f": 85.0})
		doc.save()
		self.assertEqual(weather.thresholds_for(MAIN)["heat_threshold_temp_f"], 75.0)
		self.assertEqual(weather.thresholds_for(OTHER)["heat_threshold_temp_f"], 85.0)

	def test_the_install_seeder_fills_the_declared_defaults(self):
		"""A Frappe Single stores one row per SAVED field, so on a fresh install
		every one of these reads None — and None becomes a timeout of zero one
		`int()` later."""
		from erpnext_mcp import install

		STORE.singles.pop(weather.SETTINGS_DOCTYPE, None)
		install._weather_settings()
		stored = STORE.singles.get(weather.SETTINGS_DOCTYPE) or {}
		self.assertEqual(str(stored["http_timeout_seconds"]), "10")
		self.assertEqual(str(stored["heat_threshold_temp_f"]), "80")
		self.assertEqual(str(stored["enabled"]), "1")

	def test_the_seeder_never_overwrites_a_choice_including_off(self):
		from erpnext_mcp import install

		self.weather_off()
		install._weather_settings()
		self.assertEqual(str(STORE.singles[weather.SETTINGS_DOCTYPE]["enabled"]), "0")


if __name__ == "__main__":
	unittest.main()
