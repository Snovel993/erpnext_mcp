# SPDX-License-Identifier: MIT
"""The county lookup and the Desk save path — v0.33.0's two whitelisted methods.

These are the tests for a surface with NO transport gates, which is the same
thing `test_api_mobile.py` says about the phone API and is true here for a
different reason. `mcp.handle` runs `security.authorize()` — master switch,
shared token, CIDR allowlist — before it looks a tool up. A `@frappe.whitelist()`
method reached from a Desk form runs none of that. So the checks that used to be
somebody else's job are this module's, and each is asserted BY ITS ABSENCE
FIRST: the test that a gate works is a test that the call fails without it.

SIX CLAIMS.

1. **THE SURFACE IS TWO METHODS AND THREE DOCTYPES.** `TheSaveSurfaceIsClosed`.
   There is no dispatcher and no method-name argument, so `create_journal_entry`
   is not reachable and neither is Housing Unit — asserted by enumerating what
   the module exports rather than by trusting the docstring.

2. **PERMISSION IS CHECKED HERE, NOT INHERITED.** `TheGatesRefuseWhatTheyShould`.
   The boundary tools end in `doc.save(ignore_permissions=True)`, which is right
   for them and would have handed every signed-in account a write to every
   parcel if this wrapper had trusted it. Guest, and a user denied write on the
   specific document, are both refused.

3. **A TAX LOT IS AN ALLOWLIST, NOT AN ESCAPE.** `TheTaxLotIsValidated`. The
   value goes into an ArcGIS `where` clause evaluated by somebody else's server,
   so a quote, a space, a semicolon or an `OR 1=1` is refused rather than
   neutralised in a dialect this app does not implement.

4. **x IS LONGITUDE AND y IS LATITUDE.** `TheSpatialQueryIsBuiltRight`. Getting
   that pair round the wrong way asks about a point in the Southern Ocean, which
   comes back EMPTY rather than wrong — so nothing would ever have said what
   happened. It is pinned by reading the parameters the fetch was given.

5. **AN ArcGIS ERROR IS AN HTTP 200.** `WhatTheCountyCanSendBack`. The service
   answers `{"error": {...}}` with a 200 and a JSON content type, so a client
   that checks only the status reads a malformed query as "the county has never
   heard of your parcel". It is checked by name, first.

6. **SAVING GOES THROUGH THE BOUNDARY TOOLS, NOT ROUND THEM.**
   `SavingGoesThroughTheTools`. The area disagreement still refuses, the
   containment warnings still arrive, the derived fields are still recomputed —
   asserted against the stored document, because a wrapper that wrote
   `boundary_geojson` directly would pass every other test in this file.
"""

import json
import unittest

import frappe

from erpnext_mcp import geo
from erpnext_mcp.api import gis
from erpnext_mcp.errors import ToolError

from .fixtures import MAIN
from .harness import STORE
from .test_geo import BLOCK, PARCEL_OUTLINE, ZONE_INSIDE, GeoTestCase

#: The docnames `test_geo`'s own fixtures produce. Spelled out because
#: `save_boundary` takes a DOCNAME and not a friendly name — the map has the
#: record open, so it always knows the real one, and accepting anything looser
#: here would test a leniency the browser never needs.
PARCEL_DOCNAME = "Mill Creek - ETC"
FIELD_DOCNAME = "Yellow Camp Block 3 - MC"
ZONE_DOCNAME = "YC3-Zone2 - MC"

#: What Wasco County's FeatureServer actually answers with, trimmed to the keys
#: this app reads. Field names and casing are the county's own — see
#: `COUNTIES["wasco"]["properties"]`, which is a list of spellings for exactly
#: this reason.
COUNTY_ANSWER = {
	"type": "FeatureCollection",
	"features": [
		{
			"type": "Feature",
			"properties": {
				"MapTaxlot": "2N11E35BA-01600",
				"Taxpayer": "ORCHARD HOLDINGS LLC",
				"CalculatedAcres": 330.4,
				"AccountNum": "11245",
				"Shape__Area": 14389472.5,
			},
			"geometry": PARCEL_OUTLINE,
		}
	],
}


class GISTestCase(GeoTestCase):
	"""A site with a parcel on it, and a fetch that never leaves the process."""

	def setUp(self):
		super().setUp()
		self.fetched = []
		self._fetch_before = gis._fetch
		self.addCleanup(self._restore_fetch)

	def _restore_fetch(self):
		gis._fetch = self._fetch_before

	def answer_with(self, payload):
		"""Replace the one outbound call with a recorder. Returns nothing."""

		def fake_fetch(url, params):
			self.fetched.append({"url": url, "params": dict(params)})
			if isinstance(payload, Exception):
				raise payload
			return payload

		gis._fetch = fake_fetch

	def as_user(self, user):
		frappe.local.session.user = user
		self.addCleanup(lambda: setattr(frappe.local.session, "user", "Administrator"))


# ── the closed surface ──────────────────────────────────────────────────────
class TheSaveSurfaceIsClosed(unittest.TestCase):
	"""Two methods, three doctypes, and no way to name a fourth of either."""

	def test_exactly_two_methods_are_whitelisted(self):
		exported = {
			name
			for name in dir(gis)
			if not name.startswith("_") and getattr(getattr(gis, name), "__wrapped_whitelisted__", False)
		}
		self.assertEqual(exported, {"query_county_parcels", "save_boundary"})

	def test_the_saveable_doctypes_are_the_three_that_carry_a_polygon(self):
		self.assertEqual(set(gis.SAVEABLE), {"Parcel", "Field", "Irrigation Zone"})

	def test_each_entry_names_a_real_tool_and_its_real_argument(self):
		"""A table of `(argument_name, function)` is only as good as the argument
		name, and a typo there is a `field is required` at the far end rather than
		an import error here."""
		expected = {
			"Parcel": "set_parcel_boundary",
			"Field": "set_field_boundary",
			"Irrigation Zone": "set_zone_boundary",
		}
		arguments = {"Parcel": "parcel", "Field": "field", "Irrigation Zone": "zone"}
		for doctype, (argument, tool) in gis.SAVEABLE.items():
			with self.subTest(doctype=doctype):
				self.assertEqual(tool.__name__, expected[doctype])
				self.assertEqual(argument, arguments[doctype])

	def test_the_only_hostname_is_the_countys(self):
		"""NOTHING HERE IS A GENERAL HTTP PROXY. If a URL could ever come from an
		argument, this file would be a way to make a farm's server fetch anything
		at all — so the hosts it can reach are a literal, and this is the test that
		notices one arriving from somewhere else."""
		for key, config in gis.COUNTIES.items():
			with self.subTest(county=key):
				self.assertTrue(config["url"].startswith("https://"))
				self.assertIn(".or.us/", config["url"])


# ── the gates ───────────────────────────────────────────────────────────────
class TheGatesRefuseWhatTheyShould(GISTestCase):
	def setUp(self):
		super().setUp()
		self.a_parcel(acreage=330.0)

	def test_guest_cannot_save_a_boundary(self):
		self.as_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			gis.save_boundary("Parcel", PARCEL_DOCNAME, json.dumps(PARCEL_OUTLINE))

	def test_guest_cannot_drive_the_county_lookup(self):
		"""A whitelisted method that makes an outbound request is not something to
		leave open to an unauthenticated caller, whatever it returns."""
		self.answer_with(COUNTY_ANSWER)
		self.as_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			gis.query_county_parcels(tax_lot="2N11E35BA-01600")
		self.assertEqual(self.fetched, [], "the county was asked before the caller was checked")

	def test_a_user_who_cannot_write_this_parcel_is_refused(self):
		"""THE CHECK THAT WOULD HAVE BEEN EASY TO SKIP. The boundary tools save
		with `ignore_permissions=True` — correct for them, since the MCP transport
		authorised three layers earlier — so a wrapper that trusted the framework
		would have handed every signed-in account on the site a write to every
		parcel on it."""
		STORE.denied_permissions.add(("Parcel", PARCEL_DOCNAME))
		self.addCleanup(STORE.denied_permissions.discard, ("Parcel", PARCEL_DOCNAME))
		with self.assertRaises(frappe.PermissionError):
			gis.save_boundary("Parcel", PARCEL_DOCNAME, json.dumps(PARCEL_OUTLINE))
		self.assertFalse(
			frappe.db.get_value("Parcel", PARCEL_DOCNAME, "boundary_geojson"),
			"the refusal came after the write",
		)

	def test_the_county_lookup_wants_write_on_Parcel_not_merely_read(self):
		"""The only thing an imported polygon is for is setting a parcel boundary.
		Gating on `read` would leave the site hosting an outbound fetch that a
		Family Member or an Advisor could drive."""
		self.answer_with(COUNTY_ANSWER)
		STORE.denied_permissions.add(("Parcel", "write"))
		self.addCleanup(STORE.denied_permissions.discard, ("Parcel", "write"))
		with self.assertRaises(frappe.PermissionError):
			gis.query_county_parcels(tax_lot="2N11E35BA-01600")

	def test_an_unknown_doctype_is_refused_by_name(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			gis.save_boundary("Housing Unit", "HU-0001", json.dumps(PARCEL_OUTLINE))
		self.assertIn("Housing Unit", str(caught.exception))
		self.assertIn("Parcel", str(caught.exception))

	def test_a_record_that_does_not_exist_is_refused_before_anything_else(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			gis.save_boundary("Parcel", "No Such Parcel", json.dumps(PARCEL_OUTLINE))
		self.assertIn("No Such Parcel", str(caught.exception))


# ── the tax lot, and the where clause it lands in ───────────────────────────
class TheTaxLotIsValidated(unittest.TestCase):
	"""AN ALLOWLIST AND NOT AN ESCAPE, which is the whole posture.

	The value is formatted into `MapTaxlot='…'` and evaluated by ArcGIS, whose
	`where` dialect this app does not implement and cannot test against. So
	anything that is not letters, digits, dots and hyphens is refused outright
	rather than quoted, escaped or stripped.
	"""

	CONFIG = gis.COUNTIES["wasco"]

	def test_a_real_tax_lot_becomes_the_clause(self):
		self.assertEqual(
			gis._tax_lot_clause(self.CONFIG, "2n11e35ba-01600"),
			"MapTaxlot='2N11E35BA-01600'",
		)

	def test_every_shape_that_is_not_a_tax_lot_is_refused(self):
		for value in (
			"2N11E35BA-01600' OR '1'='1",
			"'; DROP TABLE taxlots; --",
			"2N11E35BA 01600",
			"2N11E35BA%",
			'2N11"E',
			"",
			"   ",
			"-01600",
			"x" * 41,
		):
			with self.subTest(value=value):
				with self.assertRaises(ToolError):
					gis._tax_lot_clause(self.CONFIG, value)

	def test_the_clause_never_contains_a_quote_it_did_not_write(self):
		"""Belt to the brace above: whatever survives validation, the clause it
		produces has exactly two apostrophes in it."""
		clause = gis._tax_lot_clause(self.CONFIG, "2N11E35BA-01600")
		self.assertEqual(clause.count("'"), 2)


class TheCountyRegistry(unittest.TestCase):
	def test_no_county_named_means_wasco(self):
		key, config = gis._county(None)
		self.assertEqual(key, "wasco")
		self.assertEqual(config["label"], "Wasco County, Oregon")

	def test_the_way_a_person_writes_it_resolves(self):
		for spelling in ("Wasco", "wasco", "  WASCO  ", "Wasco County"):
			with self.subTest(spelling=spelling):
				self.assertEqual(gis._county(spelling)[0], "wasco")

	def test_an_unknown_county_says_which_ones_are_known(self):
		"""A county's parcel layer is a different server and a different schema
		for every county. Guessing at a URL is not a thing this can do, so the
		refusal has to say what it CAN do."""
		with self.assertRaises(ToolError) as caught:
			gis._county("Sherman")
		self.assertIn("Sherman", str(caught.exception))
		self.assertIn("wasco", str(caught.exception))


class DegreesAreDegrees(unittest.TestCase):
	def test_a_real_coordinate_passes(self):
		self.assertEqual(gis._degrees("45.6", "lat", 90.0), 45.6)
		self.assertEqual(gis._degrees(-121.18, "lon", 180.0), -121.18)

	def test_anything_off_the_globe_is_refused(self):
		for value, limit in ((91, 90.0), (-91, 90.0), (181, 180.0), (-181, 180.0)):
			with self.subTest(value=value):
				with self.assertRaises(ToolError):
					gis._degrees(value, "lat", limit)

	def test_a_word_is_refused_rather_than_coerced(self):
		for value in ("north", "", None, "NaN", "inf"):
			with self.subTest(value=value):
				with self.assertRaises(ToolError):
					gis._degrees(value, "lat", 90.0)


# ── what comes back off the wire ────────────────────────────────────────────
class WhatTheCountyCanSendBack(unittest.TestCase):
	CONFIG = gis.COUNTIES["wasco"]

	def test_a_normal_answer_becomes_this_apps_vocabulary(self):
		features, warnings = gis.parse_features(COUNTY_ANSWER, self.CONFIG)
		self.assertEqual(warnings, [])
		self.assertEqual(len(features), 1)
		one = features[0]
		self.assertEqual(one["tax_lot"], "2N11E35BA-01600")
		self.assertEqual(one["taxpayer"], "ORCHARD HOLDINGS LLC")
		self.assertEqual(one["county_acres"], 330.4)
		self.assertEqual(one["account"], "11245")
		self.assertEqual(one["geometry"]["type"], "Polygon")

	@unittest.skipUnless(geo.available(), "needs shapely>=2.0 and h3>=4.0.0")
	def test_both_acreages_are_reported_and_neither_replaces_the_other(self):
		"""TWO MEASUREMENTS, NOT ONE FACT. The county's figure is computed on its
		own projected grid and this app's is spherical; they agree to a fraction
		of a percent when the import is right, and a reader who can see both can
		tell a projection difference from the wrong parcel."""
		one = gis.parse_features(COUNTY_ANSWER, self.CONFIG)[0][0]
		self.assertIsNotNone(one["area_computed_acres"])
		self.assertAlmostEqual(one["area_computed_acres"], one["county_acres"], delta=5.0)

	def test_an_arcgis_error_is_an_http_200_and_is_caught_by_name(self):
		"""THE FAILURE THAT LOOKS LIKE AN EMPTY RESULT. A malformed query answers
		200 with `{"error": …}` and no `features` key at all, so a reader that
		checked only the status would tell somebody the county has never heard of
		their parcel."""
		with self.assertRaises(ToolError) as caught:
			gis.parse_features(
				{
					"error": {
						"code": 400,
						"message": "Unable to complete operation.",
						"details": ["bad where"],
					}
				},
				self.CONFIG,
			)
		self.assertIn("Unable to complete operation", str(caught.exception))
		self.assertIn("bad where", str(caught.exception))

	def test_something_that_is_not_a_feature_collection_is_refused(self):
		for payload in ({}, {"type": "Feature"}, {"features": "lots"}):
			with self.subTest(payload=payload):
				with self.assertRaises(ToolError):
					gis.parse_features(payload, self.CONFIG)

	def test_a_shape_that_is_not_an_area_is_dropped_and_said_so(self):
		"""Some parcel layers carry annotation geometry beside the lots. A
		boundary is an area, the three boundary tools refuse anything else, and
		handing the form a shape it will only be refused for later is worse than
		saying so here."""
		payload = {
			"type": "FeatureCollection",
			"features": [
				{
					"properties": {"MapTaxlot": "A"},
					"geometry": {"type": "Point", "coordinates": [-121.18, 45.6]},
				},
				COUNTY_ANSWER["features"][0],
			],
		}
		features, warnings = gis.parse_features(payload, self.CONFIG)
		self.assertEqual(len(features), 1)
		self.assertEqual(features[0]["tax_lot"], "2N11E35BA-01600")
		self.assertTrue(any("not areas" in line for line in warnings))

	def test_a_field_name_in_another_case_is_still_found(self):
		"""An ArcGIS layer's field names are whatever the person who published it
		typed, and a county that re-publishes in upper case must not silently
		return a parcel with no tax lot number on it."""
		payload = {
			"type": "FeatureCollection",
			"features": [
				{
					"properties": {"MAPTAXLOT": "2N11E35BA-01600", "CALCULATEDACRES": 12.5},
					"geometry": PARCEL_OUTLINE,
				}
			],
		}
		one = gis.parse_features(payload, self.CONFIG)[0][0]
		self.assertEqual(one["tax_lot"], "2N11E35BA-01600")
		self.assertEqual(one["county_acres"], 12.5)

	def test_the_esri_json_spelling_of_properties_is_read_too(self):
		"""`f=geojson` gives `properties`; `f=json` gives `attributes`. This app
		asks for the first, and reading both costs one line against the day a
		county's server ignores the parameter."""
		payload = {
			"type": "FeatureCollection",
			"features": [{"attributes": {"MapTaxlot": "2N11E35BA-01600"}, "geometry": PARCEL_OUTLINE}],
		}
		self.assertEqual(gis.parse_features(payload, self.CONFIG)[0][0]["tax_lot"], "2N11E35BA-01600")

	def test_a_flood_of_matches_is_capped_and_the_cap_is_reported(self):
		"""A SILENT TRUNCATION READS AS 'that is all there was'. If the answer is
		cut, the person choosing between the shapes has to be told."""
		payload = {
			"type": "FeatureCollection",
			"features": [COUNTY_ANSWER["features"][0]] * (gis._MAX_FEATURES + 5),
		}
		features, warnings = gis.parse_features(payload, self.CONFIG)
		self.assertEqual(len(features), gis._MAX_FEATURES)
		self.assertTrue(any(str(gis._MAX_FEATURES) in line for line in warnings))

	def test_an_acreage_that_is_not_a_number_is_dropped_rather_than_guessed(self):
		payload = {
			"type": "FeatureCollection",
			"features": [
				{"properties": {"MapTaxlot": "A", "CalculatedAcres": "unknown"}, "geometry": PARCEL_OUTLINE}
			],
		}
		self.assertIsNone(gis.parse_features(payload, self.CONFIG)[0][0]["county_acres"])


# ── the query, end to end, with the wire replaced ───────────────────────────
class TheSpatialQueryIsBuiltRight(GISTestCase):
	def setUp(self):
		super().setUp()
		self.a_parcel(acreage=330.0)
		self.answer_with(COUNTY_ANSWER)

	def test_a_tax_lot_query_asks_for_geojson_in_wgs84(self):
		"""`outSR=4326` IS THE WHOLE INTEGRATION. The layer stores Oregon
		Stateplane North in FEET (WKID 2913), and a polygon in feet parses as
		perfectly valid GeoJSON whose coordinates are somewhere near longitude
		7,600,000 — which `check_coordinates_look_like_degrees` would flag and
		nothing would fix."""
		gis.query_county_parcels(tax_lot="2N11E35BA-01600")
		params = self.fetched[0]["params"]
		self.assertEqual(params["where"], "MapTaxlot='2N11E35BA-01600'")
		self.assertEqual(params["outSR"], 4326)
		self.assertEqual(params["f"], "geojson")
		self.assertEqual(params["outFields"], "*")

	def test_x_is_longitude_and_y_is_latitude(self):
		"""THE MISTAKE THAT COMES BACK EMPTY RATHER THAN WRONG. Swapping them asks
		about 45.6°E, -121.18°N — a point in the Southern Ocean — so the answer is
		"no parcel here" and nothing ever says what actually happened."""
		gis.query_county_parcels(lat=45.6015, lon=-121.178)
		params = self.fetched[0]["params"]
		self.assertEqual(json.loads(params["geometry"]), {"x": -121.178, "y": 45.6015})
		self.assertEqual(params["geometryType"], "esriGeometryPoint")
		self.assertEqual(params["inSR"], 4326)
		self.assertEqual(params["spatialRel"], "esriSpatialRelIntersects")
		self.assertNotIn("where", params)

	def test_the_url_comes_from_the_registry_and_not_from_an_argument(self):
		gis.query_county_parcels(tax_lot="2N11E35BA-01600")
		self.assertEqual(self.fetched[0]["url"], gis.COUNTIES["wasco"]["url"])

	def test_both_a_tax_lot_and_a_point_is_refused(self):
		"""Two different questions. Answering them together would hide which one
		matched, and the whole value of the preview is knowing what you asked."""
		with self.assertRaises(frappe.ValidationError):
			gis.query_county_parcels(tax_lot="2N11E35BA-01600", lat=45.6, lon=-121.18)
		self.assertEqual(self.fetched, [])

	def test_neither_a_tax_lot_nor_a_point_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			gis.query_county_parcels()
		self.assertEqual(self.fetched, [])

	def test_a_lat_with_no_lon_is_not_half_a_query(self):
		with self.assertRaises(frappe.ValidationError):
			gis.query_county_parcels(lat=45.6)

	def test_nothing_found_is_an_answer_and_not_an_error(self):
		"""A tax lot that is not on the roll is an ordinary outcome — the number
		was mistyped, or the parcel is in the next county. It gets a sentence, not
		a traceback."""
		self.answer_with({"type": "FeatureCollection", "features": []})
		result = gis.query_county_parcels(tax_lot="2N11E35BA-99999")
		self.assertEqual(result["count"], 0)
		self.assertEqual(result["features"], [])
		self.assertTrue(any("no parcel matching" in line for line in result["warnings"]))

	def test_the_answer_names_the_county_it_came_from(self):
		result = gis.query_county_parcels(tax_lot="2N11E35BA-01600")
		self.assertEqual(result["county"], "wasco")
		self.assertEqual(result["label"], "Wasco County, Oregon")
		self.assertEqual(result["query"], {"tax_lot": "2N11E35BA-01600"})

	def test_a_lookup_leaves_an_audit_row(self):
		"""An outbound request made by the farm's server on somebody's behalf is
		exactly the kind of thing an operator later wants a record of."""
		gis.query_county_parcels(tax_lot="2N11E35BA-01600")
		rows = [
			row for row in STORE.rows("MCP Action Log") if row.get("tool_name") == "desk:query_county_parcels"
		]
		self.assertEqual(len(rows), 1)
		self.assertIn("2N11E35BA-01600", rows[0]["arguments_json"])


# ── saving, which is the part that must not be a shortcut ───────────────────
class SavingGoesThroughTheTools(GISTestCase):
	def setUp(self):
		super().setUp()
		self.a_parcel(acreage=330.0)

	def test_a_parcel_boundary_is_stored_with_everything_derived(self):
		"""A WRAPPER THAT WROTE THE FIELD DIRECTLY WOULD PASS EVERY OTHER TEST IN
		THIS FILE. This one reads the document afterwards: the centroid, the
		bounding box, the H3 coverage and the computed acreage are all functions
		of the polygon, and only the boundary tool produces them."""
		result = gis.save_boundary("Parcel", PARCEL_DOCNAME, json.dumps(PARCEL_OUTLINE))
		self.assertTrue(result["changed"])
		row = frappe.db.get_value(
			"Parcel",
			PARCEL_DOCNAME,
			[
				"boundary_geojson",
				"boundary_centroid_lat",
				"boundary_centroid_lon",
				"boundary_bbox_geojson",
				"h3_cells",
				"area_computed_acres",
			],
			as_dict=True,
		)
		self.assertEqual(json.loads(row["boundary_geojson"])["type"], "Polygon")
		self.assertAlmostEqual(row["boundary_centroid_lat"], 45.6005, places=2)
		self.assertAlmostEqual(row["boundary_centroid_lon"], -121.178, places=2)
		self.assertTrue(row["boundary_bbox_geojson"])
		self.assertTrue(json.loads(row["h3_cells"]))
		self.assertGreater(row["area_computed_acres"], 0)

	def test_a_geometry_dict_and_a_geojson_string_are_the_same_call(self):
		"""`frappe.call` hands JSON through as a parsed object when the browser
		sent one and as a string when it sent a string. `geo.parse` takes both,
		and this is what would notice a wrapper that stringified once too often."""
		gis.save_boundary("Parcel", PARCEL_DOCNAME, PARCEL_OUTLINE)
		self.assertTrue(frappe.db.get_value("Parcel", PARCEL_DOCNAME, "boundary_geojson"))

	def test_an_area_that_disagrees_with_the_acreage_is_still_refused(self):
		"""THE CHECK THE MAP MUST NOT BE A WAY ROUND. A parcel recorded at 330
		acres and a polygon enclosing four is one of the two figures being about a
		different piece of ground — usually the wrong tax lot was imported — and
		the tool refuses it. The Desk path inherits that, unchanged."""
		with self.assertRaises(frappe.ValidationError) as caught:
			gis.save_boundary("Parcel", PARCEL_DOCNAME, json.dumps(BLOCK))
		self.assertIn("330", str(caught.exception))
		self.assertFalse(frappe.db.get_value("Parcel", PARCEL_DOCNAME, "boundary_geojson"))

	def test_a_self_intersecting_polygon_is_refused(self):
		bowtie = {
			"type": "Polygon",
			"coordinates": [
				[
					[-121.1850, 45.5950],
					[-121.1710, 45.6060],
					[-121.1710, 45.5950],
					[-121.1850, 45.6060],
					[-121.1850, 45.5950],
				]
			],
		}
		with self.assertRaises(frappe.ValidationError):
			gis.save_boundary("Parcel", PARCEL_DOCNAME, json.dumps(bowtie))

	def test_a_dry_run_changes_nothing_and_says_what_it_would_do(self):
		result = gis.save_boundary("Parcel", PARCEL_DOCNAME, json.dumps(PARCEL_OUTLINE), dry_run=1)
		self.assertTrue(result["dry_run"])
		self.assertFalse(result["changed"])
		self.assertFalse(frappe.db.get_value("Parcel", PARCEL_DOCNAME, "boundary_geojson"))

	def test_the_warnings_reach_the_form_rather_than_the_log(self):
		"""Every warning the boundary tools emit is a thing somebody has to decide
		about — a block that now hangs outside its parcel, three zones left
		outside the shape. A wrapper that dropped them would turn a decision into
		a silence."""
		self.a_field(acreage=25.7)
		self.tool_data(
			"set_field_boundary",
			{"field": "Yellow Camp Block 3", "boundary_geojson": json.dumps(BLOCK)},
		)
		# A parcel outline shifted a long way east: the block it carries is now
		# nowhere near it, which is exactly what the containment check reports.
		elsewhere = {
			"type": "Polygon",
			"coordinates": [[[lon + 0.05, lat] for lon, lat in PARCEL_OUTLINE["coordinates"][0]]],
		}
		result = gis.save_boundary("Parcel", PARCEL_DOCNAME, json.dumps(elsewhere))
		self.assertTrue(result["warnings"])
		self.assertTrue(any("Yellow Camp Block 3" in line for line in result["warnings"]))

	def test_a_block_is_saved_through_its_own_tool(self):
		self.map_parcel()
		self.a_field(acreage=25.7)
		result = gis.save_boundary("Field", FIELD_DOCNAME, json.dumps(BLOCK))
		self.assertTrue(result["changed"])
		self.assertTrue(frappe.db.get_value("Field", FIELD_DOCNAME, "boundary_geojson"))

	def test_a_zone_is_saved_through_its_own_tool(self):
		self.map_parcel()
		self.a_field(acreage=25.7)
		self.tool_data(
			"set_field_boundary",
			{"field": "Yellow Camp Block 3", "boundary_geojson": json.dumps(BLOCK)},
		)
		self.a_zone(area_sq_ft=int(geo.area_acres(ZONE_INSIDE) * 43560))
		result = gis.save_boundary("Irrigation Zone", ZONE_DOCNAME, json.dumps(ZONE_INSIDE))
		self.assertTrue(result["changed"])
		self.assertTrue(frappe.db.get_value("Irrigation Zone", ZONE_DOCNAME, "boundary_geojson"))

	def test_the_company_comes_off_the_record_and_is_never_asked_for(self):
		"""THE FIXTURE IS A TWO-COMPANY SITE ON PURPOSE, and the boundary tools
		refuse to guess a company on one. A person who has the form open should
		never be asked which of their companies the parcel they are looking at is
		on — so the wrapper reads `owning_entity` off the record."""
		self.assertGreater(len(frappe.db.get_all("Company")), 1)
		gis.save_boundary("Parcel", PARCEL_DOCNAME, json.dumps(PARCEL_OUTLINE))
		self.assertEqual(frappe.db.get_value("Parcel", PARCEL_DOCNAME, "owning_entity"), MAIN)

	def test_a_save_leaves_an_audit_row_naming_the_tool_that_ran(self):
		gis.save_boundary("Parcel", PARCEL_DOCNAME, json.dumps(PARCEL_OUTLINE))
		rows = [
			row for row in STORE.rows("MCP Action Log") if row.get("tool_name") == "desk:set_parcel_boundary"
		]
		self.assertEqual(len(rows), 1)
		self.assertIn(PARCEL_DOCNAME, rows[0]["arguments_json"])


class TheMcpSwitchesAreNotTheDeskGate(GISTestCase):
	"""`allow_set_parcel_boundary` is the MODEL's leash, and reading it here would
	mean an operator who distrusts the AI also loses the ability to trace a parcel
	by hand — which is not what the switch says and not what they asked for.

	This is the same call `api/__init__.py` made for the phone, asserted rather
	than described because it is the kind of decision a later refactor "fixes".
	"""

	def setUp(self):
		super().setUp()
		self.configure(enabled=1, allow_set_parcel_boundary=0, allow_create_parcel=1)
		self.a_parcel(acreage=330.0)

	def test_a_person_can_draw_a_boundary_the_model_may_not_set(self):
		self.assertIn(
			"switched off on this site",
			self.tool_error(
				"set_parcel_boundary",
				{"parcel": "Mill Creek", "boundary_geojson": json.dumps(PARCEL_OUTLINE)},
			),
		)
		result = gis.save_boundary("Parcel", PARCEL_DOCNAME, json.dumps(PARCEL_OUTLINE))
		self.assertTrue(result["changed"])
