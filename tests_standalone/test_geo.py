# SPDX-License-Identifier: MIT
"""Boundaries: the shapes, what is derived from them, and the geofence.

These tests are SKIPPED on a bench without shapely and h3, and that is itself
part of the design being tested — the five geospatial tools declare an
`available` predicate, so a site missing the libraries loses those five by name
rather than failing to load the other hundred and sixteen. `test_settings` and
`test_mutate_tools` cover the skipped-site behaviour; this module covers what
happens when the libraries are there.

Five things these tests are really about.

THE H3 FILL MUST BE A SUPERSET. H3's default polygon fill keeps cells whose
CENTRE is inside the shape, and an orchard block is smaller than one cell at
resolutions 6, 7 and 8 — so the default returns an EMPTY SET for a real field.
There is a test asserting every stored resolution has at least one cell, because
that empty set is what a geofence built on the obvious code would silently
return.

THE PREFILTER MUST NOT DROP ANSWERS. `find_fields_containing_point` narrows with
the bounding box, which is a guaranteed superset, and then tests exactly. There
is a test for a point inside the bbox but outside the polygon, because that is
the case a bbox-only implementation gets wrong.

AREA IS SPHERICAL, NOT DEGREES SQUARED. `shapely.area` on lat/lon coordinates is
a number in degrees squared, which is not an area of anything. There is a test
against a rectangle whose true area is known.

CONTAINMENT IS REPORTED, NOT ENFORCED. A zone outside its block is a warning,
because a shared water line really does cross a boundary. There are tests for
both directions and for the block-has-no-boundary case.

THE POLYGON IS COMPLIANCE EVIDENCE. `WovenNotShadow` proves removing it breaks an
operational answer and a regulatory one at once — the geofence stops answering
AND the spray record loses the thing an auditor checks a GPS fix against.
"""

import json
import unittest

from erpnext_mcp import geo

from .fixtures import MAIN, OTHER, V12TestCase
from .harness import STORE

#: A rectangle on Dry Hollow Road, about 0.004 by 0.003 degrees. Small enough to be a
#: real block and therefore small enough to expose the H3 centre-fill problem.
BLOCK = {
	"type": "Polygon",
	"coordinates": [
		[
			[-121.1800, 45.6000],
			[-121.1760, 45.6000],
			[-121.1760, 45.6030],
			[-121.1800, 45.6030],
			[-121.1800, 45.6000],
		]
	],
}

#: A smaller rectangle wholly inside BLOCK.
ZONE_INSIDE = {
	"type": "Polygon",
	"coordinates": [
		[
			[-121.1790, 45.6005],
			[-121.1775, 45.6005],
			[-121.1775, 45.6020],
			[-121.1790, 45.6020],
			[-121.1790, 45.6005],
		]
	],
}

#: The same size, shifted east so it hangs off the block — the shared-water-line
#: case the containment check must report rather than refuse.
ZONE_OUTSIDE = {
	"type": "Polygon",
	"coordinates": [
		[
			[-121.1750, 45.6005],
			[-121.1735, 45.6005],
			[-121.1735, 45.6020],
			[-121.1750, 45.6020],
			[-121.1750, 45.6005],
		]
	],
}

#: A rectangle that CONTAINS `BLOCK` with room around it — the parcel the block
#: sits on. Roughly 0.014 by 0.011 degrees, which is about 300 acres.
PARCEL_OUTLINE = {
	"type": "Polygon",
	"coordinates": [
		[
			[-121.1850, 45.5950],
			[-121.1710, 45.5950],
			[-121.1710, 45.6060],
			[-121.1850, 45.6060],
			[-121.1850, 45.5950],
		]
	],
}

INSIDE_POINT = {"lat": 45.6015, "lon": -121.1780}
OUTSIDE_POINT = {"lat": 45.7000, "lon": -121.3000}

ALL_ON = {
	"allow_create_parcel": 1,
	"allow_create_field": 1,
	"allow_update_field": 1,
	"allow_get_field": 1,
	"allow_list_fields": 1,
	"allow_create_irrigation_zone": 1,
	"allow_get_irrigation_zone": 1,
	"allow_list_irrigation_zones": 1,
	"allow_get_parcel_field_summary": 1,
	"allow_set_field_boundary": 1,
	"allow_set_zone_boundary": 1,
	"allow_set_parcel_boundary": 1,
	"allow_get_parcel": 1,
	"allow_list_parcels": 1,
	"allow_create_housing_unit": 1,
	"allow_update_housing_unit": 1,
	"allow_find_fields_containing_point": 1,
	"allow_find_fields_by_h3_cell": 1,
	"allow_import_field_boundary_geojson": 1,
}


def shifted(geometry: dict, east: float = 0.0, north: float = 0.0) -> dict:
	ring = geometry["coordinates"][0]
	return {
		"type": "Polygon",
		"coordinates": [[[lon + east, lat + north] for lon, lat in ring]],
	}


@unittest.skipUnless(geo.available(), "needs shapely>=2.0 and h3>=4.0.0")
class GeoTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def a_parcel(self, parcel_name="Mill Creek", acreage=131.43, company=MAIN):
		return self.tool_data(
			"create_parcel",
			{"owning_entity": company, "parcel_name": parcel_name, "acreage": acreage},
		)

	def a_field(self, field_name="Yellow Camp Block 3", parcel="Mill Creek", acreage=25.7, **kw):
		payload = {"parcel": parcel, "field_name": field_name, "acreage": acreage}
		payload.update(kw)
		return self.tool_data("create_field", payload)

	def a_zone(self, zone_name="YC3-Zone2", field="Yellow Camp Block 3", area_sq_ft=110000, **kw):
		payload = {"field": field, "zone_name": zone_name, "area_sq_ft": area_sq_ft}
		payload.update(kw)
		return self.tool_data("create_irrigation_zone", payload)

	def map_parcel(self, parcel_name="Mill Creek", geometry=None):
		"""Trace an already-registered parcel. Separate from `a_mapped_parcel` so a
		test can register the parcel, put things on it, and only then draw the
		outline — which is the order every one of the containment tests needs."""
		return self.tool_data(
			"set_parcel_boundary",
			{
				"parcel": parcel_name,
				"boundary_geojson": json.dumps(geometry or PARCEL_OUTLINE),
			},
		)

	def a_mapped_parcel(self, parcel_name="Mill Creek", acreage=330.0, geometry=None, **kw):
		self.a_parcel(parcel_name=parcel_name, acreage=acreage, **kw)
		return self.map_parcel(parcel_name, geometry)

	def a_mapped_field(self, **kw):
		self.a_parcel()
		self.a_field(**kw)
		return self.tool_data(
			"set_field_boundary",
			{"field": "Yellow Camp Block 3", "boundary_geojson": json.dumps(BLOCK)},
		)


# ── the geometry itself ─────────────────────────────────────────────────────
@unittest.skipUnless(geo.available(), "needs shapely>=2.0 and h3>=4.0.0")
class Geometry(unittest.TestCase):
	def test_area_is_spherical_not_degrees_squared(self):
		"""shapely.area on lat/lon gives degrees squared, which is not an area of
		anything.

		The rectangle is 0.004 deg of longitude by 0.003 of latitude at 45.6N.
		By hand: 0.004 x 111,320 x cos(45.6) = 311.5m, 0.003 x 111,132 = 333.4m,
		so 103,865 m2 = 25.67 acres. The spherical figure agrees to about 0.2%,
		which is the accuracy claim in the module docstring made checkable."""
		acres = geo.area_acres(BLOCK)
		self.assertAlmostEqual(acres, 25.67, delta=0.1)

	def test_a_hole_is_subtracted(self):
		"""A well pad cut out of a block is ground that is not planted."""
		solid = geo.area_acres(BLOCK)
		with_hole = geo.area_acres(
			{"type": "Polygon", "coordinates": BLOCK["coordinates"] + ZONE_INSIDE["coordinates"]}
		)
		self.assertLess(with_hole, solid)
		self.assertAlmostEqual(solid - with_hole, geo.area_acres(ZONE_INSIDE), places=2)

	def test_the_h3_fill_is_never_empty_at_any_stored_resolution(self):
		"""THE test of this module. H3's default fill keeps cells whose CENTRE is
		inside the shape, and this block is smaller than one cell at resolutions
		6, 7 and 8 — so the default returns nothing and an index built on it
		answers 'in no field' for a point plainly in one."""
		cells = geo.h3_cells(BLOCK)
		for resolution in geo.H3_RESOLUTIONS:
			with self.subTest(resolution=resolution):
				self.assertTrue(cells[str(resolution)], f"resolution {resolution} filled to nothing")

	def test_the_cell_containing_an_interior_point_is_in_the_stored_set(self):
		"""What the superset property is FOR: a point inside the block must be in
		a cell the block claims, at every resolution."""
		cells = geo.h3_cells(BLOCK)
		for resolution in geo.H3_RESOLUTIONS:
			with self.subTest(resolution=resolution):
				cell = geo.cell_for_point(INSIDE_POINT["lat"], INSIDE_POINT["lon"], resolution)
				self.assertIn(cell, cells[str(resolution)])

	def test_the_centroid_of_a_rectangle_is_its_middle(self):
		shape = geo.validate_geometry(BLOCK)
		latitude, longitude = geo.centroid(shape)
		self.assertAlmostEqual(latitude, 45.6015, places=4)
		self.assertAlmostEqual(longitude, -121.1780, places=4)

	def test_the_bounding_box_contains_the_shape(self):
		shape = geo.validate_geometry(BLOCK)
		bounds = geo.bbox_bounds(geo.bbox_geojson(shape))
		self.assertEqual(bounds, (-121.18, 45.6, -121.176, 45.603))

	def test_the_boundary_counts_as_inside(self):
		"""A pick on the headland is in the block. A geofence that excludes its
		own edge tells the picker they are nowhere."""
		shape = geo.validate_geometry(BLOCK)
		self.assertTrue(geo.covers_point(shape, 45.6000, -121.1780))


@unittest.skipUnless(geo.available(), "needs shapely>=2.0 and h3>=4.0.0")
class Parsing(unittest.TestCase):
	def error(self, payload, **kwargs):
		from erpnext_mcp.errors import ToolError

		with self.assertRaises(ToolError) as caught:
			geo.parse(payload, **kwargs)
		return str(caught.exception)

	def test_a_bare_geometry_is_accepted(self):
		self.assertEqual(geo.parse(json.dumps(BLOCK))["type"], "Polygon")

	def test_a_feature_is_unwrapped(self):
		self.assertEqual(
			geo.parse({"type": "Feature", "properties": {}, "geometry": BLOCK})["type"], "Polygon"
		)

	def test_a_single_feature_collection_is_unwrapped(self):
		"""Whichever of the three your export button produced."""
		collection = {
			"type": "FeatureCollection",
			"features": [{"type": "Feature", "properties": {}, "geometry": BLOCK}],
		}
		self.assertEqual(geo.parse(collection)["type"], "Polygon")

	def test_a_multi_feature_collection_names_the_import_tool(self):
		collection = {
			"type": "FeatureCollection",
			"features": [
				{"type": "Feature", "properties": {}, "geometry": BLOCK},
				{"type": "Feature", "properties": {}, "geometry": ZONE_INSIDE},
			],
		}
		self.assertIn("import_field_boundary_geojson", self.error(collection))

	def test_broken_json_is_refused_with_the_parser_error(self):
		self.assertIn("not valid JSON", self.error("{not json"))

	def test_a_point_is_refused_because_it_is_not_an_area(self):
		message = self.error({"type": "Point", "coordinates": [-121.18, 45.6]})
		self.assertIn("not an area", message)

	def test_an_unclosed_ring_is_refused(self):
		message = self.error(
			{
				"type": "Polygon",
				"coordinates": [[[-121.18, 45.6], [-121.176, 45.6], [-121.176, 45.603], [-121.18, 45.605]]],
			}
		)
		self.assertIn("not closed", message)
		self.assertIn("does not enclose anything", message)

	def test_a_ring_with_too_few_positions_is_refused(self):
		message = self.error(
			{"type": "Polygon", "coordinates": [[[-121.18, 45.6], [-121.176, 45.6], [-121.18, 45.6]]]}
		)
		self.assertIn("at least", message)

	def test_coordinates_off_earth_are_refused_with_the_likely_cause(self):
		message = self.error(
			{
				"type": "Polygon",
				"coordinates": [[[45.6, -121.18], [45.6, -121.17], [45.61, -121.17], [45.6, -121.18]]],
			}
		)
		self.assertIn("not on Earth", message)
		self.assertIn("wrong way round", message)

	def test_a_self_intersecting_polygon_is_refused(self):
		"""A bow tie has an area a computer will report and a containment test
		nobody can trust. It is what two swapped vertices produce."""
		bowtie = {
			"type": "Polygon",
			"coordinates": [
				[
					[-121.180, 45.600],
					[-121.176, 45.603],
					[-121.176, 45.600],
					[-121.180, 45.603],
					[-121.180, 45.600],
				]
			],
		}
		from erpnext_mcp.errors import ToolError

		with self.assertRaises(ToolError) as caught:
			geo.validate_geometry(geo.parse(bowtie))
		self.assertIn("not a valid polygon", str(caught.exception))

	def test_a_multipolygon_is_accepted(self):
		multi = {"type": "MultiPolygon", "coordinates": [BLOCK["coordinates"], ZONE_OUTSIDE["coordinates"]]}
		self.assertEqual(geo.parse(multi)["type"], "MultiPolygon")
		self.assertGreater(geo.area_acres(multi), geo.area_acres(BLOCK))

	def test_a_shape_the_size_of_a_county_is_flagged(self):
		big = shifted(BLOCK)
		big["coordinates"][0][1][0] += 2.0
		big["coordinates"][0][2][0] += 2.0
		notes = geo.check_coordinates_look_like_degrees(big, "boundary_geojson")
		self.assertTrue(any("seventy miles" in note for note in notes))

	def test_null_island_is_flagged(self):
		tiny = {
			"type": "Polygon",
			"coordinates": [[[0.0, 0.0], [0.001, 0.0], [0.001, 0.001], [0.0, 0.0]]],
		}
		notes = geo.check_coordinates_look_like_degrees(tiny, "boundary_geojson")
		self.assertTrue(any("Gulf of Guinea" in note for note in notes))


# ── set_field_boundary ──────────────────────────────────────────────────────
class SetFieldBoundary(GeoTestCase):
	def test_it_stores_the_polygon_and_derives_everything_else(self):
		data = self.a_mapped_field()
		self.assertTrue(data["changed"])
		row = STORE.get_raw("Field", "Yellow Camp Block 3 - MC")
		self.assertTrue(row["boundary_geojson"])
		self.assertTrue(row["boundary_bbox_geojson"])
		self.assertTrue(row["h3_cells"])
		self.assertAlmostEqual(row["boundary_centroid_lat"], 45.6015, places=4)
		self.assertAlmostEqual(row["boundary_centroid_lon"], -121.178, places=4)

	def test_the_round_trip_reports_the_computed_area(self):
		data = self.a_mapped_field()
		self.assertAlmostEqual(data["area_computed_acres"], 25.67, delta=0.1)

	def test_it_stores_cells_at_every_resolution(self):
		self.a_mapped_field()
		cells = geo.stored_cells(STORE.get_raw("Field", "Yellow Camp Block 3 - MC")["h3_cells"])
		self.assertEqual(sorted(int(key) for key in cells), list(geo.H3_RESOLUTIONS))
		for resolution, entries in cells.items():
			with self.subTest(resolution=resolution):
				self.assertTrue(entries)

	def test_get_field_reports_cell_counts_rather_than_every_cell(self):
		"""A register of forty blocks does not need four thousand cell ids in a
		model's context; find_fields_by_h3_cell queries them server-side."""
		self.a_mapped_field()
		data = self.tool_data("get_field", {"field": "Yellow Camp Block 3"})
		self.assertTrue(data["has_boundary"])
		self.assertEqual(sorted(data["h3_cell_counts"]), ["10", "6", "7", "8", "9"])
		self.assertNotIn("h3_cells", data)

	def test_a_polygon_wildly_at_odds_with_the_acreage_is_refused(self):
		self.a_parcel()
		self.a_field(acreage=100)
		error = self.tool_error(
			"set_field_boundary",
			{"field": "Yellow Camp Block 3", "boundary_geojson": json.dumps(BLOCK)},
		)
		self.assertIn("different piece of ground", error)
		self.assertIn("Nothing was changed", error)
		self.assertFalse(STORE.get_raw("Field", "Yellow Camp Block 3 - MC").get("boundary_geojson"))

	def test_a_modest_disagreement_is_warned_about_and_both_figures_kept(self):
		"""A deed, a GIS trace and a tape measure routinely disagree."""
		self.a_parcel()
		self.a_field(acreage=24.0)
		data = self.tool_data(
			"set_field_boundary",
			{"field": "Yellow Camp Block 3", "boundary_geojson": json.dumps(BLOCK)},
		)
		self.assertTrue(data["changed"])
		self.assertEqual(data["acreage_recorded"], 24.0)
		self.assertTrue(any("routinely disagree" in w for w in data["warnings"]))

	def test_no_recorded_acreage_is_not_a_disagreement(self):
		self.a_parcel()
		self.a_field(acreage=0)
		data = self.tool_data(
			"set_field_boundary",
			{"field": "Yellow Camp Block 3", "boundary_geojson": json.dumps(BLOCK)},
		)
		self.assertTrue(any("No acreage was recorded" in w for w in data["warnings"]))

	def test_an_unmapped_parcel_is_said_out_loud_rather_than_left_as_a_silent_gap(self):
		"""'Nothing was checked' and 'it checked out' are different answers.

		From v0.12.0 to v0.31.0 this warning was unconditional, because a Parcel
		carried no polygon at all. v0.32.0 gave it one, so the sentence now means
		something narrower and truer: this particular parcel has not been traced.
		"""
		data = self.a_mapped_field()
		self.assertTrue(any("has no boundary of its own" in w for w in data["warnings"]))
		self.assertIsNone(data["boundary_contained_in_parcel"])

	def test_dry_run_computes_everything_and_writes_nothing(self):
		self.a_parcel()
		self.a_field()
		data = self.tool_data(
			"set_field_boundary",
			{
				"field": "Yellow Camp Block 3",
				"boundary_geojson": json.dumps(BLOCK),
				"dry_run": True,
			},
		)
		self.assertTrue(data["dry_run"])
		self.assertFalse(data["changed"])
		self.assertGreater(data["area_computed_acres"], 22.0)
		self.assertFalse(STORE.get_raw("Field", "Yellow Camp Block 3 - MC").get("boundary_geojson"))

	def test_invalid_geojson_is_refused(self):
		self.a_parcel()
		self.a_field()
		self.assertIn(
			"not valid JSON",
			self.tool_error(
				"set_field_boundary", {"field": "Yellow Camp Block 3", "boundary_geojson": "{oops"}
			),
		)

	def test_setting_it_twice_replaces_it(self):
		self.a_mapped_field()
		first = STORE.get_raw("Field", "Yellow Camp Block 3 - MC")["boundary_centroid_lon"]
		self.tool_data(
			"set_field_boundary",
			{
				"field": "Yellow Camp Block 3",
				"boundary_geojson": json.dumps(shifted(BLOCK, east=0.001)),
			},
		)
		self.assertNotEqual(
			STORE.get_raw("Field", "Yellow Camp Block 3 - MC")["boundary_centroid_lon"], first
		)

	def test_the_switch_is_off_by_default(self):
		self.a_parcel()
		self.a_field()
		self.configure(enabled=1, allow_create_parcel=1, allow_create_field=1)
		self.assertIn(
			"switched off",
			self.tool_error(
				"set_field_boundary",
				{"field": "Yellow Camp Block 3", "boundary_geojson": json.dumps(BLOCK)},
			),
		)

	def test_it_is_audited(self):
		self.a_mapped_field()
		self.assertAudited("set_field_boundary", "Success")


# ── set_zone_boundary and nesting ───────────────────────────────────────────
class SetZoneBoundary(GeoTestCase):
	def setUp(self):
		super().setUp()
		self.a_mapped_field()

	def test_a_zone_inside_its_block_is_reported_as_contained(self):
		self.a_zone(area_sq_ft=int(geo.area_acres(ZONE_INSIDE) * 43560))
		data = self.tool_data(
			"set_zone_boundary", {"zone": "YC3-Zone2", "boundary_geojson": json.dumps(ZONE_INSIDE)}
		)
		self.assertTrue(data["boundary_contained_in_field"])
		self.assertEqual(data["warnings"], [])

	def test_a_zone_outside_its_block_is_warned_about_not_refused(self):
		"""A shared water line really does cross a boundary, a pump house sits on
		the headland, a mainline runs down an easement. Refusing would make those
		unrecordable."""
		self.a_zone(area_sq_ft=int(geo.area_acres(ZONE_OUTSIDE) * 43560))
		data = self.tool_data(
			"set_zone_boundary", {"zone": "YC3-Zone2", "boundary_geojson": json.dumps(ZONE_OUTSIDE)}
		)
		self.assertFalse(data["boundary_contained_in_field"])
		self.assertTrue(any("shared line" in w for w in data["warnings"]))
		self.assertTrue(data["changed"])
		self.assertTrue(STORE.get_raw("Irrigation Zone", "YC3-Zone2 - MC")["boundary_geojson"])

	def test_a_block_with_no_boundary_gives_a_null_answer_not_a_false_one(self):
		"""'We could not check' and 'we checked and it is outside' are different
		answers, and reporting the first as the second is a lie."""
		self.a_field("Block 9", acreage=5)
		self.a_zone("Z9", field="Block 9", area_sq_ft=int(geo.area_acres(ZONE_INSIDE) * 43560))
		data = self.tool_data(
			"set_zone_boundary", {"zone": "Z9", "boundary_geojson": json.dumps(ZONE_INSIDE)}
		)
		self.assertIsNone(data["boundary_contained_in_field"])
		self.assertTrue(any("has no boundary of its own" in w for w in data["warnings"]))

	def test_setting_a_block_boundary_reports_zones_that_now_fall_outside_it(self):
		self.a_zone(area_sq_ft=int(geo.area_acres(ZONE_OUTSIDE) * 43560))
		self.tool_data(
			"set_zone_boundary", {"zone": "YC3-Zone2", "boundary_geojson": json.dumps(ZONE_OUTSIDE)}
		)
		data = self.tool_data(
			"set_field_boundary",
			{"field": "Yellow Camp Block 3", "boundary_geojson": json.dumps(BLOCK)},
		)
		self.assertEqual(data["zones_outside_boundary"], ["YC3-Zone2 - MC"])

	def test_a_zone_polygon_wildly_at_odds_with_its_area_is_refused(self):
		self.a_zone(area_sq_ft=1000)
		error = self.tool_error(
			"set_zone_boundary", {"zone": "YC3-Zone2", "boundary_geojson": json.dumps(ZONE_INSIDE)}
		)
		self.assertIn("different zone", error)
		self.assertIn("Nothing was changed", error)

	def test_the_derived_fields_land_on_the_zone(self):
		self.a_zone(area_sq_ft=int(geo.area_acres(ZONE_INSIDE) * 43560))
		self.tool_data(
			"set_zone_boundary", {"zone": "YC3-Zone2", "boundary_geojson": json.dumps(ZONE_INSIDE)}
		)
		row = STORE.get_raw("Irrigation Zone", "YC3-Zone2 - MC")
		self.assertTrue(row["h3_cells"])
		self.assertTrue(row["boundary_bbox_geojson"])
		self.assertGreater(row["area_computed_acres"], 0)


# ── find_fields_containing_point ────────────────────────────────────────────
class FindFieldsContainingPoint(GeoTestCase):
	def setUp(self):
		super().setUp()
		self.a_mapped_field()

	def test_a_point_inside_finds_the_block(self):
		data = self.tool_data("find_fields_containing_point", INSIDE_POINT)
		self.assertEqual(data["match_count"], 1)
		self.assertEqual(data["fields"][0]["name"], "Yellow Camp Block 3 - MC")

	def test_a_point_outside_finds_nothing(self):
		self.assertEqual(self.tool_data("find_fields_containing_point", OUTSIDE_POINT)["match_count"], 0)

	def test_a_point_on_the_boundary_counts_as_inside(self):
		data = self.tool_data("find_fields_containing_point", {"lat": 45.6000, "lon": -121.1780})
		self.assertEqual(data["match_count"], 1)
		self.assertTrue(data["boundary_inclusive"])

	def test_a_point_inside_the_bbox_but_outside_an_l_shaped_polygon_is_excluded(self):
		"""The case a bounding-box-only implementation gets wrong. The bbox is the
		prefilter; the exact test is what settles it."""
		self.a_field("L Block", acreage=0)
		l_shape = {
			"type": "Polygon",
			"coordinates": [
				[
					[-121.1700, 45.6000],
					[-121.1660, 45.6000],
					[-121.1660, 45.6010],
					[-121.1680, 45.6010],
					[-121.1680, 45.6030],
					[-121.1700, 45.6030],
					[-121.1700, 45.6000],
				]
			],
		}
		self.tool_data("set_field_boundary", {"field": "L Block", "boundary_geojson": json.dumps(l_shape)})
		# In the bounding box of the L, but in the notch that the L excludes.
		notch = {"lat": 45.6025, "lon": -121.1665}
		data = self.tool_data("find_fields_containing_point", notch)
		self.assertEqual(data["match_count"], 0)
		self.assertGreaterEqual(data["candidates_after_bbox"], 1)

	def test_it_reports_the_points_own_h3_cell_at_every_resolution(self):
		data = self.tool_data("find_fields_containing_point", INSIDE_POINT)
		self.assertEqual(sorted(data["h3_cells"]), ["10", "6", "7", "8", "9"])

	def test_it_says_how_many_blocks_have_no_boundary_at_all(self):
		"""An empty result on a half-mapped farm means 'not inside any MAPPED
		block', not 'not on the farm', and those are different things to act on."""
		self.a_field("Unmapped Block", acreage=5)
		data = self.tool_data("find_fields_containing_point", OUTSIDE_POINT)
		self.assertEqual(data["match_count"], 0)
		self.assertEqual(data["fields_without_a_boundary"], 1)
		self.assertIn("not on the farm", data["note"])

	def test_a_point_off_earth_is_refused(self):
		error = self.tool_error("find_fields_containing_point", {"lat": 145.0, "lon": -121.0})
		self.assertIn("not a point on Earth", error)
		self.assertIn("wrong way round", error)

	def test_both_coordinates_are_required(self):
		self.assertIn(
			"lat and lon are both required",
			self.tool_error("find_fields_containing_point", {"lat": 45.6}),
		)

	def test_it_scopes_to_one_company(self):
		self.a_parcel("Far Field", acreage=50, company=OTHER)
		self.a_field("Overlapping Block", "Far Field", acreage=25.7)
		self.tool_data(
			"set_field_boundary",
			{"field": "Overlapping Block", "boundary_geojson": json.dumps(BLOCK)},
		)
		everywhere = self.tool_data("find_fields_containing_point", INSIDE_POINT)
		self.assertEqual(everywhere["match_count"], 2)
		scoped = self.tool_data("find_fields_containing_point", {**INSIDE_POINT, "company": MAIN})
		self.assertEqual(scoped["match_count"], 1)

	def test_it_is_read_only(self):
		before = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		self.tool_data("find_fields_containing_point", INSIDE_POINT)
		after = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		before.pop("MCP Action Log", None)
		after.pop("MCP Action Log", None)
		self.assertEqual(before, after)


# ── find_fields_by_h3_cell ──────────────────────────────────────────────────
class FindFieldsByH3Cell(GeoTestCase):
	def setUp(self):
		super().setUp()
		self.a_mapped_field()

	def cell(self, resolution):
		return geo.cell_for_point(INSIDE_POINT["lat"], INSIDE_POINT["lon"], resolution)

	def test_a_cell_at_a_stored_resolution_finds_the_block(self):
		for resolution in geo.H3_RESOLUTIONS:
			with self.subTest(resolution=resolution):
				data = self.tool_data("find_fields_by_h3_cell", {"cell": self.cell(resolution)})
				self.assertEqual(data["match_count"], 1)
				self.assertEqual(data["matched_at_resolution"], resolution)

	def test_a_finer_cell_is_rolled_up_to_the_finest_stored_resolution(self):
		data = self.tool_data("find_fields_by_h3_cell", {"cell": self.cell(13)})
		self.assertEqual(data["cell_resolution"], 13)
		self.assertEqual(data["matched_at_resolution"], 10)
		self.assertEqual(data["match_count"], 1)

	def test_a_coarser_cell_is_matched_by_rolling_the_blocks_cells_up(self):
		data = self.tool_data("find_fields_by_h3_cell", {"cell": self.cell(3)})
		self.assertEqual(data["cell_resolution"], 3)
		self.assertEqual(data["match_count"], 1)

	def test_a_cell_somewhere_else_finds_nothing(self):
		elsewhere = geo.cell_for_point(OUTSIDE_POINT["lat"], OUTSIDE_POINT["lon"], 9)
		self.assertEqual(self.tool_data("find_fields_by_h3_cell", {"cell": elsewhere})["match_count"], 0)

	def test_an_invalid_cell_is_refused(self):
		self.assertIn(
			"not a valid H3 cell", self.tool_error("find_fields_by_h3_cell", {"cell": "not-a-cell"})
		)

	def test_it_says_a_match_means_touching_not_containing(self):
		data = self.tool_data("find_fields_by_h3_cell", {"cell": self.cell(9)})
		self.assertIn("TOUCHES", data["note"])
		self.assertIn("find_fields_containing_point", data["note"])

	def test_two_blocks_sharing_a_cell_both_come_back(self):
		self.a_field("Neighbour Block", acreage=25.7)
		self.tool_data(
			"set_field_boundary",
			{
				"field": "Neighbour Block",
				"boundary_geojson": json.dumps(shifted(BLOCK, east=0.0041)),
			},
		)
		data = self.tool_data("find_fields_by_h3_cell", {"cell": self.cell(6)})
		self.assertEqual(data["match_count"], 2)


# ── import_field_boundary_geojson ───────────────────────────────────────────
class ImportFieldBoundaries(GeoTestCase):
	def setUp(self):
		super().setUp()
		self.a_parcel()
		self.a_field("Block One", acreage=25.7)
		self.a_field("Block Two", acreage=25.7)

	def collection(self, *features):
		return {"type": "FeatureCollection", "features": list(features)}

	def feature(self, name, geometry=None, **properties):
		properties.setdefault("parcel_hint", "Mill Creek")
		return {
			"type": "Feature",
			"properties": {"field_name": name, **properties},
			"geometry": geometry or BLOCK,
		}

	def test_it_is_dry_run_by_default(self):
		data = self.tool_data(
			"import_field_boundary_geojson",
			{"feature_collection": self.collection(self.feature("Block One"))},
		)
		self.assertTrue(data["dry_run"])
		self.assertEqual(data["would_set"], 1)
		self.assertFalse(STORE.get_raw("Field", "Block One - MC").get("boundary_geojson"))

	def test_apply_sets_the_boundaries(self):
		data = self.tool_data(
			"import_field_boundary_geojson",
			{
				"feature_collection": self.collection(
					self.feature("Block One"),
					self.feature("Block Two", shifted(BLOCK, east=0.0041)),
				),
				"apply": True,
			},
		)
		self.assertEqual(sorted(data["set"]), ["Block One - MC", "Block Two - MC"])
		self.assertTrue(STORE.get_raw("Field", "Block One - MC")["boundary_geojson"])
		self.assertTrue(STORE.get_raw("Field", "Block One - MC")["h3_cells"])

	def test_a_json_string_is_accepted_as_well_as_an_object(self):
		data = self.tool_data(
			"import_field_boundary_geojson",
			{"feature_collection": json.dumps(self.collection(self.feature("Block One")))},
		)
		self.assertEqual(data["would_set"], 1)

	def test_one_malformed_feature_does_not_stop_the_others(self):
		"""The opposite of import_farm_app_fields, on purpose: that tool CREATES
		records so a half-run leaves a farm to reconcile. This one only sets a
		field on records that already exist."""
		data = self.tool_data(
			"import_field_boundary_geojson",
			{
				"feature_collection": self.collection(
					self.feature("Block One"),
					{"type": "Feature", "properties": {}, "geometry": BLOCK},
					self.feature("Block Two", shifted(BLOCK, east=0.0041)),
				),
				"apply": True,
			},
		)
		self.assertEqual(sorted(data["set"]), ["Block One - MC", "Block Two - MC"])
		bad = data["results"][1]
		self.assertEqual(bad["action"], "error")
		self.assertIn("field_name", bad["reason"])

	def test_a_feature_naming_an_unregistered_block_is_skipped_not_created(self):
		data = self.tool_data(
			"import_field_boundary_geojson",
			{"feature_collection": self.collection(self.feature("Nowhere Block"))},
		)
		entry = data["results"][0]
		self.assertEqual(entry["action"], "skip")
		self.assertIn("never creates a Field", entry["reason"])
		self.assertIsNone(STORE.get_raw("Field", "Nowhere Block - MC"))

	def test_a_feature_with_bad_geometry_is_reported_by_index(self):
		data = self.tool_data(
			"import_field_boundary_geojson",
			{
				"feature_collection": self.collection(
					self.feature("Block One", {"type": "Point", "coordinates": [-121.18, 45.6]})
				)
			},
		)
		self.assertEqual(data["results"][0]["action"], "error")
		self.assertIn("not an area", data["results"][0]["reason"])

	def test_the_area_rule_applies_per_feature(self):
		self.a_field("Tiny Block", acreage=1)
		data = self.tool_data(
			"import_field_boundary_geojson",
			{"feature_collection": self.collection(self.feature("Tiny Block"))},
		)
		self.assertEqual(data["results"][0]["action"], "error")
		self.assertIn("different piece of ground", data["results"][0]["reason"])

	def test_a_repeated_block_in_one_collection_is_refused_for_the_second(self):
		data = self.tool_data(
			"import_field_boundary_geojson",
			{"feature_collection": self.collection(self.feature("Block One"), self.feature("Block One"))},
		)
		self.assertEqual(data["would_set"], 1)
		self.assertIn("appears twice", data["results"][1]["reason"])

	def test_a_default_parcel_covers_features_with_no_hint(self):
		feature = {
			"type": "Feature",
			"properties": {"field_name": "Block One"},
			"geometry": BLOCK,
		}
		data = self.tool_data(
			"import_field_boundary_geojson",
			{"feature_collection": self.collection(feature), "parcel": "Mill Creek"},
		)
		self.assertEqual(data["would_set"], 1)

	def test_no_hint_and_no_default_is_reported_per_feature(self):
		feature = {"type": "Feature", "properties": {"field_name": "Block One"}, "geometry": BLOCK}
		data = self.tool_data(
			"import_field_boundary_geojson", {"feature_collection": self.collection(feature)}
		)
		self.assertIn("no default `parcel`", data["results"][0]["reason"])

	def test_something_that_is_not_a_feature_collection_is_refused_outright(self):
		self.assertIn(
			"FeatureCollection",
			self.tool_error("import_field_boundary_geojson", {"feature_collection": BLOCK}),
		)

	def test_it_reports_when_a_boundary_is_being_replaced(self):
		self.tool_data(
			"import_field_boundary_geojson",
			{"feature_collection": self.collection(self.feature("Block One")), "apply": True},
		)
		data = self.tool_data(
			"import_field_boundary_geojson",
			{"feature_collection": self.collection(self.feature("Block One"))},
		)
		self.assertTrue(data["results"][0]["replaces_existing"])

	def test_the_switch_is_off_by_default(self):
		self.configure(enabled=1, allow_create_parcel=1, allow_create_field=1)
		self.assertIn(
			"switched off",
			self.tool_error(
				"import_field_boundary_geojson",
				{"feature_collection": self.collection(self.feature("Block One"))},
			),
		)


# ── set_parcel_boundary, and the gap it closes ──────────────────────────────
class SetParcelBoundary(GeoTestCase):
	"""v0.32.0. THE OUTER SHAPE, and the answer set_field_boundary spent nineteen
	releases apologising for not having.

	From v0.12.0 to v0.31.0 every call of `set_field_boundary` ended with a line
	saying a parcel had no boundary, so nothing had checked the block sat inside
	its parcel. It does now, in both directions: setting either shape reports the
	disagreement, and neither refuses it — a planting that predates a deed split
	really does straddle the line.
	"""

	def test_it_stores_the_polygon_and_derives_everything_else(self):
		self.a_mapped_parcel()
		row = STORE.tables["Parcel"]["Mill Creek - ETC"]
		self.assertTrue(row["boundary_geojson"])
		self.assertAlmostEqual(row["boundary_centroid_lat"], 45.6005, delta=0.001)
		self.assertAlmostEqual(row["boundary_centroid_lon"], -121.178, delta=0.001)
		self.assertTrue(row["boundary_bbox_geojson"])
		self.assertTrue(row["area_computed_acres"])

	def test_it_stores_cells_at_every_resolution(self):
		"""The H3 fill has to be a SUPERSET. A centre-based fill returns an empty
		set for a shape smaller than one cell, and an index built on it answers
		'on no parcel' for a point plainly on one."""
		self.a_mapped_parcel()
		cells = json.loads(STORE.tables["Parcel"]["Mill Creek - ETC"]["h3_cells"])
		self.assertEqual(sorted(int(key) for key in cells), list(geo.H3_RESOLUTIONS))
		for resolution, entries in cells.items():
			self.assertTrue(entries, f"resolution {resolution} came back empty")

	def test_the_computed_area_is_a_second_opinion_on_the_deeded_one(self):
		data = self.a_mapped_parcel()
		self.assertAlmostEqual(data["area_computed_acres"], 330, delta=5)
		self.assertEqual(data["acreage_recorded"], 330.0)
		self.assertEqual(data["warnings"], [])

	def test_a_polygon_wildly_at_odds_with_the_acreage_is_refused(self):
		"""Past 25% one of the two figures is about a different piece of ground —
		and on a PARCEL the recorded acreage is usually the one to trust, because
		it is the number the assessor, the deed and the tax bill agree on."""
		self.a_parcel(parcel_name="Mill Creek", acreage=40)
		message = self.tool_error(
			"set_parcel_boundary",
			{"parcel": "Mill Creek", "boundary_geojson": json.dumps(PARCEL_OUTLINE)},
		)
		self.assertIn("different piece of ground", message)
		self.assertIn("Nothing was changed", message)
		self.assertFalse(STORE.tables["Parcel"]["Mill Creek - ETC"].get("boundary_geojson"))

	def test_a_few_percent_is_a_warning_and_not_a_refusal(self):
		"""A deed, an assessor's map and a GIS trace routinely disagree."""
		self.a_parcel(parcel_name="Mill Creek", acreage=350)
		data = self.tool_data(
			"set_parcel_boundary",
			{"parcel": "Mill Creek", "boundary_geojson": json.dumps(PARCEL_OUTLINE)},
		)
		self.assertTrue(data["changed"])
		self.assertTrue(any("routinely disagree" in w for w in data["warnings"]))

	def test_no_recorded_acreage_is_not_a_disagreement(self):
		self.a_parcel(parcel_name="Mill Creek", acreage=0)
		data = self.tool_data(
			"set_parcel_boundary",
			{"parcel": "Mill Creek", "boundary_geojson": json.dumps(PARCEL_OUTLINE)},
		)
		self.assertTrue(any("No acreage was recorded" in w for w in data["warnings"]))

	def test_a_self_intersecting_boundary_is_refused(self):
		self.a_parcel(parcel_name="Mill Creek", acreage=0)
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
		self.assertIn(
			"not a valid polygon",
			self.tool_error(
				"set_parcel_boundary",
				{"parcel": "Mill Creek", "boundary_geojson": json.dumps(bowtie)},
			),
		)

	def test_a_point_is_not_an_area(self):
		self.a_parcel(parcel_name="Mill Creek", acreage=0)
		self.assertIn(
			"not an area",
			self.tool_error(
				"set_parcel_boundary",
				{
					"parcel": "Mill Creek",
					"boundary_geojson": json.dumps({"type": "Point", "coordinates": [-121.18, 45.6]}),
				},
			),
		)

	def test_dry_run_computes_everything_and_writes_nothing(self):
		self.a_parcel(parcel_name="Mill Creek", acreage=330)
		data = self.tool_data(
			"set_parcel_boundary",
			{
				"parcel": "Mill Creek",
				"boundary_geojson": json.dumps(PARCEL_OUTLINE),
				"dry_run": True,
			},
		)
		self.assertTrue(data["dry_run"])
		self.assertFalse(data["changed"])
		self.assertTrue(data["area_computed_acres"])
		self.assertFalse(STORE.tables["Parcel"]["Mill Creek - ETC"].get("boundary_geojson"))

	def test_a_block_inside_the_parcel_is_not_reported(self):
		self.a_parcel(parcel_name="Mill Creek", acreage=330)
		self.a_field()
		self.tool_data(
			"set_field_boundary",
			{"field": "Yellow Camp Block 3", "boundary_geojson": json.dumps(BLOCK)},
		)
		self.assertEqual(self.map_parcel()["outside_boundary"], {})

	def test_a_block_hanging_over_the_deed_line_is_reported_and_not_refused(self):
		"""A planting that predates a deed split really does straddle the line."""
		self.a_parcel(parcel_name="Mill Creek", acreage=330)
		self.a_field()
		self.tool_data(
			"set_field_boundary",
			{
				"field": "Yellow Camp Block 3",
				"boundary_geojson": json.dumps(shifted(BLOCK, east=0.010)),
			},
		)
		data = self.map_parcel()
		self.assertEqual(data["outside_boundary"]["Field"], ["Yellow Camp Block 3 - MC"])
		self.assertTrue(any("straddle a deed line" in w for w in data["warnings"]))
		self.assertTrue(data["changed"])

	def test_an_unmapped_block_is_not_reported_as_outside(self):
		"""It is UNMAPPED, which is a different answer. Listing fifty names that
		mean nothing would bury the two that do."""
		self.a_parcel(parcel_name="Mill Creek", acreage=330)
		self.a_field()
		self.assertEqual(self.map_parcel()["outside_boundary"], {})

	def test_a_cabin_outside_the_parcel_is_reported(self):
		"""v0.32.0 gave Housing Unit coordinates, which is what makes a point
		checkable against a parcel outline at all."""
		self.a_parcel(parcel_name="Mill Creek", acreage=330)
		self.tool_data(
			"create_housing_unit",
			{
				"parcel": "Mill Creek",
				"unit_name": "MC-Cabin-01",
				"gps_latitude": 45.7000,
				"gps_longitude": -121.3000,
			},
		)
		data = self.tool_data(
			"set_parcel_boundary",
			{"parcel": "Mill Creek", "boundary_geojson": json.dumps(PARCEL_OUTLINE)},
		)
		self.assertEqual(data["outside_boundary"]["Housing Unit"], ["MC-Cabin-01 - MC"])

	def test_a_cabin_on_the_parcel_is_not_reported(self):
		self.a_parcel(parcel_name="Mill Creek", acreage=330)
		self.tool_data(
			"create_housing_unit",
			{
				"parcel": "Mill Creek",
				"unit_name": "MC-Cabin-01",
				"gps_latitude": 45.6015,
				"gps_longitude": -121.1780,
			},
		)
		data = self.tool_data(
			"set_parcel_boundary",
			{"parcel": "Mill Creek", "boundary_geojson": json.dumps(PARCEL_OUTLINE)},
		)
		self.assertEqual(data["outside_boundary"], {})

	def test_setting_it_twice_replaces_it(self):
		self.a_mapped_parcel()
		first = STORE.tables["Parcel"]["Mill Creek - ETC"]["boundary_centroid_lon"]
		self.tool_data(
			"set_parcel_boundary",
			{
				"parcel": "Mill Creek",
				"boundary_geojson": json.dumps(shifted(PARCEL_OUTLINE, east=0.001)),
			},
		)
		second = STORE.tables["Parcel"]["Mill Creek - ETC"]["boundary_centroid_lon"]
		self.assertAlmostEqual(second - first, 0.001, delta=0.0002)

	def test_the_switch_is_off_by_default(self):
		self.a_parcel(parcel_name="Mill Creek", acreage=330)
		self.configure(enabled=1)
		self.assertIn(
			"switched off",
			self.tool_error(
				"set_parcel_boundary",
				{"parcel": "Mill Creek", "boundary_geojson": json.dumps(PARCEL_OUTLINE)},
			),
		)

	def test_it_is_audited(self):
		self.a_mapped_parcel()
		self.assertAudited("set_parcel_boundary", "Success")

	def test_get_parcel_hands_back_the_shape_and_list_parcels_does_not(self):
		"""A boundary is kilobytes of coordinates and a register would carry one
		per row. The list says `mapped` and a centroid; the single read is the one
		a map calls and gets the polygon from."""
		self.a_mapped_parcel()
		single = self.tool_data("get_parcel", {"parcel": "Mill Creek"})
		self.assertTrue(single["boundary_geojson"])
		self.assertTrue(single["mapped"])
		self.assertEqual(single["boundary_centroid"]["lat"], round(45.6005, 7))
		listed = self.tool_data("list_parcels", {"owning_entity": MAIN})["parcels"][0]
		self.assertTrue(listed["mapped"])
		self.assertNotIn("boundary_geojson", listed)

	def test_an_unmapped_parcel_reports_no_centroid_rather_than_null_island(self):
		"""[0, 0] is a real place in the Gulf of Guinea, and it is what an unset
		Float pair looks like. A map that flies there looks exactly like a map
		showing you where something is."""
		self.a_parcel(parcel_name="Mill Creek", acreage=330)
		single = self.tool_data("get_parcel", {"parcel": "Mill Creek"})
		self.assertFalse(single["mapped"])
		self.assertIsNone(single["boundary_centroid"])
		self.assertIsNone(single["boundary_geojson"])


# ── the two shapes check each other ─────────────────────────────────────────
class TheBlockIsCheckedAgainstItsParcel(GeoTestCase):
	"""THE GAP v0.32.0 CLOSES, from the other side. `set_field_boundary` reports
	whether the block sits inside the parcel — reported and never enforced, and
	null rather than false when the parcel has no shape to check against."""

	def test_a_block_inside_its_parcel_is_reported_as_contained(self):
		self.a_mapped_parcel()
		self.a_field()
		data = self.tool_data(
			"set_field_boundary",
			{"field": "Yellow Camp Block 3", "boundary_geojson": json.dumps(BLOCK)},
		)
		self.assertTrue(data["boundary_contained_in_parcel"])
		self.assertFalse(any("not fully inside" in w for w in data["warnings"]))

	def test_a_block_outside_its_parcel_is_warned_about_and_not_refused(self):
		self.a_mapped_parcel()
		self.a_field()
		data = self.tool_data(
			"set_field_boundary",
			{
				"field": "Yellow Camp Block 3",
				"boundary_geojson": json.dumps(shifted(BLOCK, east=0.010)),
			},
		)
		self.assertFalse(data["boundary_contained_in_parcel"])
		self.assertTrue(any("not fully inside" in w for w in data["warnings"]))
		self.assertTrue(data["changed"])

	def test_an_unmapped_parcel_gives_a_null_answer_not_a_false_one(self):
		"""'We could not check' and 'we checked and it is outside' are different
		answers, and only one of them is somebody's problem."""
		data = self.a_mapped_field()
		self.assertIsNone(data["boundary_contained_in_parcel"])
		self.assertTrue(any("has no boundary of its own" in w for w in data["warnings"]))


# ── the boundary is compliance evidence ─────────────────────────────────────
class WovenNotShadow(GeoTestCase):
	"""Removing the polygon has to break an operational answer AND a regulatory
	one, or it belongs in a separate mapping register rather than on the block.

	It breaks both, and more sharply than any other field on the doctype: the
	geofence stops answering, and the spray record loses the one thing an auditor
	can check a GPS fix against.
	"""

	def setUp(self):
		super().setUp()
		self.a_mapped_field(last_spray_date="2026-05-15", food_safety_zone=True)

	def test_the_polygon_is_what_ties_a_spray_record_to_ground_somebody_can_verify(self):
		# REGULATORY: the WPS answer is "Block 3 was sprayed on the 15th", and the
		# polygon is what makes that checkable against a GPS track.
		field = self.tool_data("get_field", {"field": "Yellow Camp Block 3"})
		self.assertEqual(field["last_spray_date"], "2026-05-15")
		self.assertTrue(field["has_boundary"])
		# OPERATIONAL: it is also what answers "can the crew standing here go in".
		point = self.tool_data("find_fields_containing_point", INSIDE_POINT)
		self.assertEqual(point["fields"][0]["last_spray_date"], "2026-05-15")

		STORE.tables["Field"]["Yellow Camp Block 3 - MC"]["boundary_geojson"] = None
		STORE.tables["Field"]["Yellow Camp Block 3 - MC"]["boundary_bbox_geojson"] = None
		STORE.tables["Field"]["Yellow Camp Block 3 - MC"]["h3_cells"] = None

		# REGULATORY: the spray date survives, but nothing ties it to ground.
		field = self.tool_data("get_field", {"field": "Yellow Camp Block 3"})
		self.assertEqual(field["last_spray_date"], "2026-05-15")
		self.assertFalse(field["has_boundary"])
		# OPERATIONAL: the geofence has no answer for somebody standing in it.
		self.assertEqual(self.tool_data("find_fields_containing_point", INSIDE_POINT)["match_count"], 0)

	def test_the_index_goes_with_it_so_nothing_can_join_on_the_block_either(self):
		cell = geo.cell_for_point(INSIDE_POINT["lat"], INSIDE_POINT["lon"], 9)
		self.assertEqual(self.tool_data("find_fields_by_h3_cell", {"cell": cell})["match_count"], 1)
		STORE.tables["Field"]["Yellow Camp Block 3 - MC"]["h3_cells"] = None
		self.assertEqual(self.tool_data("find_fields_by_h3_cell", {"cell": cell})["match_count"], 0)

	def test_an_unmapped_block_is_reported_rather_than_looking_like_an_absence(self):
		"""The failure this guards against is the quiet one: a geofence that says
		no because the ground was never mapped, read as a policy decision."""
		STORE.tables["Field"]["Yellow Camp Block 3 - MC"]["boundary_geojson"] = None
		data = self.tool_data("find_fields_containing_point", INSIDE_POINT)
		self.assertEqual(data["match_count"], 0)
		self.assertEqual(data["fields_without_a_boundary"], 1)
		self.assertIn("not on the farm", data["note"])

	def test_the_derived_area_is_a_second_opinion_on_the_recorded_one(self):
		"""Which is the compliance use: an acreage nobody can check is an acreage
		on a per-acre application rate nobody can check either."""
		summary = self.tool_data("get_parcel_field_summary", {"parcel": "Mill Creek"})
		self.assertEqual(summary["planted_acreage"], 25.7)
		field = self.tool_data("get_field", {"field": "Yellow Camp Block 3"})
		self.assertAlmostEqual(field["area_computed_acres"], 25.67, delta=0.1)
