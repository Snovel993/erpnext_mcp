# SPDX-License-Identifier: MIT
"""Boundaries: parsing them, checking them, and deriving what can be indexed.

WHY A POLYGON IS COMPLIANCE EVIDENCE AND NOT DECORATION. "Which field was
sprayed" is a Worker Protection Standard answer, "which zone got the water test"
is an FSMA Subpart E answer, and "was the worker in an authorised area" is both
a payroll question and a food-safety one. Every one of those resolves to a shape
on the ground. Without the shape the record says a name, and a name is not
evidence that anybody can check against a GPS fix.

EVERYTHING DERIVED IS DERIVED ON SAVE, NEVER TYPED. Centroid, bounding box, H3
coverage and computed area are all functions of `boundary_geojson`. A caller can
set the polygon and nothing else; a field a caller could edit independently is a
field that will disagree with the polygon, and the disagreement will be found by
a geofence saying no to somebody standing in the right place.

THE H3 FILL USES `contain="overlap"`, AND THAT IS THE MOST IMPORTANT LINE IN
THIS MODULE. H3's default polygon fill keeps cells whose *centre* lies inside the
shape. For an orchard block that returns **zero cells at resolutions 6, 7 and
8** — the block is smaller than one cell, so no cell centre is inside it. A
coarse filter built on the default fill would answer "this point is in no field"
for most of the farm, which is exactly the failure mode a geofence must not
have: a false negative that reads like a policy decision. `overlap` returns every
cell the shape touches, which is a true superset and therefore safe to filter on.

AREA IS SPHERICAL, AND SAID SO. `shapely` computes area in the units of its
coordinates, and the coordinates here are degrees — so `.area` is degrees
squared, which is not an area of anything. The figure below is the standard
spherical-excess line integral over the ring, which is exact on a sphere and
differs from a WGS84 ellipsoidal area by well under 1% at these latitudes. That
is far inside the 5% and 25% thresholds it is compared against, and pulling in a
projection library to close a gap smaller than the disagreement between a deed
and a GIS trace would be precision theatre.

BOTH LIBRARIES ARE OPTIONAL AT IMPORT TIME. They are declared dependencies and a
normal install has them, but this app's promise is that installing it cannot
break a site — so a bench missing them loses the geospatial tools (which say so,
by name) rather than failing to load the other hundred and sixteen. The
structural checks below need neither library and always run, so a boundary that
is not valid GeoJSON is refused on any site.
"""

import json
import math

from .args import as_float
from .errors import ToolError

try:  # pragma: no cover - exercised by whichever branch the test bench has
	import shapely
	from shapely.geometry import Point
	from shapely.geometry import shape as shapely_shape
	from shapely.validation import explain_validity

	HAVE_SHAPELY = True
except Exception:  # pragma: no cover - a bench without the dependency
	shapely = None
	HAVE_SHAPELY = False

try:  # pragma: no cover
	import h3

	HAVE_H3 = True
except Exception:  # pragma: no cover
	h3 = None
	HAVE_H3 = False

#: Mean Earth radius in metres, as WGS84 defines the semi-major axis. Used for
#: the spherical area only; nothing here claims ellipsoidal accuracy.
EARTH_RADIUS_M = 6378137.0

SQ_METRES_PER_ACRE = 4046.8564224

#: The H3 resolutions a boundary is indexed at. Roughly: 6 ≈ 36 km², 7 ≈ 5 km²,
#: 8 ≈ 0.74 km², 9 ≈ 0.10 km², 10 ≈ 0.015 km². Five of them because the useful
#: query changes with the question — a parcel-wide sweep wants 6, "is this pick
#: inside this block" wants 10 — and because storing them costs almost nothing:
#: a 130-acre field is about fifty cells at resolution 10.
H3_RESOLUTIONS = (6, 7, 8, 9, 10)

#: Geometry types a boundary may be. A Point or a LineString is not an area, and
#: a GeometryCollection is a container somebody should have unpacked.
ALLOWED_TYPES = ("Polygon", "MultiPolygon")

#: How far the computed area may drift from the acreage somebody typed before it
#: is worth saying something, and before it is worth refusing. A deed and a GIS
#: trace routinely disagree by a few percent and both are "right"; a quarter is
#: not a disagreement, it is one of the two numbers being about a different
#: piece of ground.
AREA_WARN_RATIO = 0.05
AREA_REFUSE_RATIO = 0.25

#: NDVI is an index, not a percentage, and it runs -1 to 1: water and bare soil
#: read negative. Clamping the floor to zero would make a flooded block
#: indistinguishable from an unmeasured one.
NDVI_MIN = -1.0
NDVI_MAX = 1.0


def available() -> bool:
	"""Can this site compute a boundary's derived values at all?"""
	return HAVE_SHAPELY and HAVE_H3


def requires_sentence() -> str:
	missing = [
		name for name, present in (("shapely>=2.0", HAVE_SHAPELY), ("h3>=4.0.0", HAVE_H3)) if not present
	]
	return (
		f"the Python package(s) {', '.join(missing)}, which this app declares as dependencies — "
		"install them into the bench's environment with "
		f"`./env/bin/pip install {' '.join(missing)}` and restart"
	)


def require() -> None:
	if not available():
		raise ToolError(f"this site is missing {requires_sentence()}. Nothing was changed.")


# ── structural validation, which needs no library ───────────────────────────
def parse(text, label: str = "boundary_geojson") -> dict:
	"""A GeoJSON geometry from a string or a dict, or a ToolError saying why not.

	Accepts a bare geometry, a Feature, or a FeatureCollection of exactly one
	Feature — a caller exporting from QGIS gets whichever of those the export
	button produced, and refusing two of the three is a round trip for nothing.
	"""
	if isinstance(text, (dict, list)):
		payload = text
	else:
		raw = str(text or "").strip()
		if not raw:
			raise ToolError(f"{label} is required (a GeoJSON Polygon or MultiPolygon)")
		try:
			payload = json.loads(raw)
		except json.JSONDecodeError as error:
			raise ToolError(f"{label} is not valid JSON: {error}. Nothing was changed.") from None

	if not isinstance(payload, dict):
		raise ToolError(f"{label} must be a GeoJSON object, not a {type(payload).__name__}.")

	kind = str(payload.get("type") or "")
	if kind == "FeatureCollection":
		features = payload.get("features") or []
		if len(features) != 1:
			raise ToolError(
				f"{label} is a FeatureCollection of {len(features)} features. One boundary is one "
				"shape — pass the geometry, or use import_field_boundary_geojson for a whole "
				"collection. Nothing was changed."
			)
		payload = features[0]
		kind = str(payload.get("type") or "")
	if kind == "Feature":
		payload = payload.get("geometry") or {}
		kind = str(payload.get("type") or "")

	if kind not in ALLOWED_TYPES:
		raise ToolError(
			f"{label} is a {kind or 'shape with no type'}; a boundary has to be a "
			f"{' or a '.join(ALLOWED_TYPES)}. A Point or a LineString is not an area. "
			"Nothing was changed."
		)

	rings = _rings(payload)
	if not rings:
		raise ToolError(f"{label} has no coordinates. Nothing was changed.")
	for index, ring in enumerate(rings, start=1):
		_check_ring(ring, index, label)
	return {"type": kind, "coordinates": payload.get("coordinates")}


def _rings(geometry: dict) -> list:
	"""Every linear ring in the geometry, outer and holes alike, as flat a list."""
	coordinates = geometry.get("coordinates")
	if not isinstance(coordinates, list):
		return []
	if geometry.get("type") == "Polygon":
		return [ring for ring in coordinates if isinstance(ring, list)]
	out = []
	for polygon in coordinates:
		if isinstance(polygon, list):
			out.extend(ring for ring in polygon if isinstance(ring, list))
	return out


def _check_ring(ring, index: int, label: str) -> None:
	if len(ring) < 4:
		raise ToolError(
			f"{label} ring {index} has {len(ring)} position(s). A closed ring needs at least "
			"four, because the first and last are the same point. Nothing was changed."
		)
	for position in ring:
		if not isinstance(position, (list, tuple)) or len(position) < 2:
			raise ToolError(
				f"{label} ring {index} has a position that is not a [longitude, latitude] pair. "
				"Nothing was changed."
			)
		lon, lat = float(position[0]), float(position[1])
		if not -180.0 <= lon <= 180.0 or not -90.0 <= lat <= 90.0:
			raise ToolError(
				f"{label} ring {index} has the position [{lon}, {lat}], which is not on Earth. "
				"GeoJSON is [longitude, latitude] — a latitude past 90 usually means the pair is "
				"the wrong way round. Nothing was changed."
			)
	if list(ring[0][:2]) != list(ring[-1][:2]):
		raise ToolError(
			f"{label} ring {index} is not closed: it starts at {list(ring[0][:2])} and ends at "
			f"{list(ring[-1][:2])}. A boundary that does not come back to itself does not enclose "
			"anything. Nothing was changed."
		)


# ── geometric validation and derivation, which need shapely ─────────────────
def validate_geometry(geometry: dict, label: str = "boundary_geojson"):
	"""A shapely geometry, or a ToolError naming what is wrong with the shape.

	Self-intersection is the one worth spelling out: a bow-tie polygon has an
	area a computer will happily report and a containment test nobody can trust,
	and it is what a hand-drawn trace produces when two vertices get swapped.
	"""
	require()
	try:
		shape = shapely_shape(geometry)
	except Exception as error:
		raise ToolError(f"{label} is not a shape shapely can read: {error}. Nothing was changed.") from None
	if shape.is_empty:
		raise ToolError(f"{label} encloses nothing. Nothing was changed.")
	if not shape.is_valid:
		raise ToolError(
			f"{label} is not a valid polygon: {explain_validity(shape)}. A self-intersecting "
			"boundary has an area a computer will report and a containment test nobody can "
			"trust — it is usually two vertices in the wrong order. Nothing was changed."
		)
	return shape


def centroid(shape) -> tuple:
	"""`(lat, lon)` of the shape's centroid, rounded to about a centimetre.

	`representative_point` rather than `centroid` for a shape whose centroid
	falls outside it — a C-shaped block around a farmstead is a real thing, and a
	map that centres on the farmstead is centring on somebody else's ground.
	"""
	point = shape.centroid
	if not shape.covers(point):
		point = shape.representative_point()
	return round(point.y, 7), round(point.x, 7)


def bbox_geojson(shape) -> str:
	"""The shape's bounding box, as a GeoJSON Polygon string.

	Stored because it is the only *safe* cheap prefilter for a containment
	question: a bounding box is a guaranteed superset of the shape it bounds, so
	a candidate set built from it can never miss the right answer.
	"""
	min_lon, min_lat, max_lon, max_lat = shape.bounds
	return json.dumps(
		{
			"type": "Polygon",
			"coordinates": [
				[
					[min_lon, min_lat],
					[max_lon, min_lat],
					[max_lon, max_lat],
					[min_lon, max_lat],
					[min_lon, min_lat],
				]
			],
		},
		separators=(",", ":"),
	)


def bbox_bounds(text) -> tuple | None:
	"""`(min_lon, min_lat, max_lon, max_lat)` back out of a stored bbox string."""
	try:
		ring = json.loads(str(text or ""))["coordinates"][0]
	except Exception:
		return None
	lons = [float(position[0]) for position in ring]
	lats = [float(position[1]) for position in ring]
	return min(lons), min(lats), max(lons), max(lats)


def area_acres(geometry: dict) -> float:
	"""Polygon area in acres, by spherical excess. See the module docstring.

	Holes are subtracted, which is what makes a block with a well pad cut out of
	it report the ground that is actually planted.
	"""
	square_metres = 0.0
	if geometry.get("type") == "Polygon":
		square_metres = _polygon_area(geometry.get("coordinates") or [])
	else:
		for polygon in geometry.get("coordinates") or []:
			square_metres += _polygon_area(polygon)
	return round(square_metres / SQ_METRES_PER_ACRE, 4)


def _polygon_area(rings) -> float:
	if not rings:
		return 0.0
	total = abs(_ring_area(rings[0]))
	for hole in rings[1:]:
		total -= abs(_ring_area(hole))
	return max(0.0, total)


def _ring_area(ring) -> float:
	"""Signed area of one ring in square metres, by the spherical line integral.

	The standard formulation (Chamberlain & Duquette, and what `geojson-area`
	implements): sum over each vertex of the change in longitude across its
	neighbours, weighted by the sine of its latitude.
	"""
	count = len(ring)
	if count < 3:
		return 0.0
	total = 0.0
	for index in range(count):
		lower = ring[index]
		middle = ring[(index + 1) % count]
		upper = ring[(index + 2) % count]
		total += (math.radians(float(upper[0])) - math.radians(float(lower[0]))) * math.sin(
			math.radians(float(middle[1]))
		)
	return total * EARTH_RADIUS_M * EARTH_RADIUS_M / 2.0


def h3_cells(geometry: dict, resolutions=H3_RESOLUTIONS) -> dict:
	"""Every H3 cell the shape TOUCHES, per resolution. Read the module docstring.

	`contain="overlap"` is not a tuning choice. The default fill keeps cells
	whose centre is inside the shape, and an orchard block is smaller than one
	cell at resolutions 6 through 8 — so the default returns an empty set for
	most fields, and an index built on it answers "in no field" for a point that
	is plainly in one.
	"""
	require()
	shape = h3.geo_to_h3shape(geometry)
	out = {}
	for resolution in resolutions:
		try:
			cells = h3.h3shape_to_cells_experimental(shape, resolution, contain="overlap")
		except Exception:  # pragma: no cover - an h3 build without the experimental fill
			cells = h3.h3shape_to_cells(shape, resolution)
		out[str(resolution)] = sorted(cells)
	return out


def cell_for_point(lat: float, lon: float, resolution: int) -> str:
	require()
	return h3.latlng_to_cell(float(lat), float(lon), int(resolution))


#: The resolution a point fix is filed at. ~66 m edge, which is the scale at
#: which "was the crew at the shed or at the block" is a question with an answer
#: and "which side of the row" is not.
POINT_RESOLUTION = 9


def coordinates(
	args: dict, *, prefix: str = "", required: bool = True, tail: str = "Nothing was created."
) -> tuple:
	"""`(latitude, longitude)` in decimal degrees from a call, or `(None, None)`.

	ONE READING OF A PAIR OF COORDINATES FOR THE WHOLE APP. `log_shift_location`
	had this inline and a training session's badge scan needed the same thing an
	hour later; two copies of "is this a point on Earth" is two chances for one
	surface to accept the pair the wrong way round.

	`lat` AND `lon` ARE ACCEPTED because that is what a phone's location API hands
	back and what `find_fields_containing_point` calls them. `prefix` gives a
	record that carries more than one fix its own pair — `scan_latitude` and
	`scan_lat` for the badge scan — without a second function.

	ALL OR NOTHING, ALWAYS. A latitude with no longitude is a point on a line
	rather than a place, and half a fix stored as if it were a whole one is worse
	than none — so where the pair is optional, one half alone is still refused
	rather than silently dropped. That refusal is the difference between "the
	phone had no signal" and "the phone had a fix and this app kept half of it".
	"""
	pairs = {}
	for full, short in (("latitude", "lat"), ("longitude", "lon")):
		value = args.get(f"{prefix}{full}")
		if value in (None, ""):
			value = args.get(f"{prefix}{short}")
		if value in (None, ""):
			pairs[full] = None
			continue
		pairs[full] = as_float(value, f"{prefix}{full}")

	latitude, longitude = pairs["latitude"], pairs["longitude"]
	if latitude is None and longitude is None:
		if required:
			raise ToolError(
				f"{prefix}latitude and {prefix}longitude are required, in decimal degrees "
				f"({prefix}lat and {prefix}lon are accepted for them). A fix with no position is "
				f"a timestamp. {tail}"
			)
		return None, None
	if latitude is None or longitude is None:
		missing = f"{prefix}latitude" if latitude is None else f"{prefix}longitude"
		raise ToolError(
			f"{missing} is required and the other half of the pair was given without it. Half a "
			f"fix is a point on a line rather than a place, and storing it as if it were a whole "
			f"one is worse than storing nothing. Send both or neither. {tail}"
		)
	if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
		raise ToolError(
			f"[{longitude}, {latitude}] is not a point on Earth. Latitude runs -90 to 90 and "
			"longitude -180 to 180 — a latitude past 90 is almost always the pair the wrong way "
			f"round, which is the one version of this mistake a computer can catch. {tail}"
		)
	return latitude, longitude


def point_cell(latitude, longitude, resolution: int = POINT_RESOLUTION) -> str:
	"""The H3 cell one fix falls in, or "" on a site without the library.

	NEVER RAISES, which is the difference from `cell_for_point` and the reason
	this exists beside it. An H3 index is a convenience for grouping fixes that
	happened in the same place; a bench without the library should lose the
	grouping and keep the coordinates, not lose the record.
	"""
	if latitude is None or longitude is None or not available():
		return ""
	try:
		return cell_for_point(float(latitude), float(longitude), int(resolution))
	except Exception:  # pragma: no cover - an h3 build that will not index a point
		return ""


def cell_resolution(cell: str) -> int:
	require()
	if not h3.is_valid_cell(cell):
		raise ToolError(f"{cell!r} is not a valid H3 cell index.")
	return int(h3.get_resolution(cell))


def cell_parent(cell: str, resolution: int) -> str:
	require()
	return h3.cell_to_parent(cell, int(resolution))


def covers_point(shape, lat: float, lon: float) -> bool:
	"""Is the point inside the shape, boundary included?

	`covers` rather than `contains`, so a pick recorded on the edge of a block is
	in the block. A geofence that excludes its own boundary is a geofence that
	tells a picker standing on the headland that they are nowhere.
	"""
	return bool(shape.covers(Point(float(lon), float(lat))))


def covers_shape(outer, inner) -> bool:
	return bool(outer.covers(inner))


def check_coordinates_look_like_degrees(geometry: dict, label: str) -> list:
	"""Warn about the mistakes that produce a valid shape in the wrong place.

	Both are shapes a validator accepts and a human would spot instantly: a
	polygon a whole degree across is about seventy miles of orchard, and one at
	the null island intersection is what an unset coordinate looks like.
	"""
	notes = []
	rings = _rings(geometry)
	lons = [float(position[0]) for ring in rings for position in ring]
	lats = [float(position[1]) for ring in rings for position in ring]
	if not lons:
		return notes
	if (max(lons) - min(lons)) > 1.0 or (max(lats) - min(lats)) > 1.0:
		notes.append(
			f"{label} spans more than a degree of latitude or longitude — roughly seventy miles. "
			"That is a county, not a block. Check the coordinates are not in the wrong order or "
			"the wrong units."
		)
	if all(abs(value) < 0.01 for value in lons + lats):
		notes.append(
			f"{label} sits at [0, 0] in the Gulf of Guinea, which is what an unset coordinate looks like."
		)
	return notes


def area_disagreement(entered: float, computed: float) -> tuple:
	"""`(ratio, verdict)` comparing typed acreage against the polygon's own.

	`verdict` is `""`, `"warn"` or `"refuse"`. An entered acreage of zero is not
	a disagreement — it is a figure nobody has supplied, and the polygon is now
	the better answer.
	"""
	entered = float(entered or 0)
	computed = float(computed or 0)
	if entered <= 0 or computed <= 0:
		return 0.0, ""
	ratio = abs(computed - entered) / entered
	if ratio > AREA_REFUSE_RATIO:
		return round(ratio, 4), "refuse"
	if ratio > AREA_WARN_RATIO:
		return round(ratio, 4), "warn"
	return round(ratio, 4), ""


def derive(geometry: dict, label: str = "boundary_geojson") -> dict:
	"""Everything a stored boundary carries, from the polygon and nothing else."""
	shape = validate_geometry(geometry, label)
	latitude, longitude = centroid(shape)
	return {
		"shape": shape,
		"boundary_geojson": json.dumps(geometry, separators=(",", ":")),
		"boundary_centroid_lat": latitude,
		"boundary_centroid_lon": longitude,
		"boundary_bbox_geojson": bbox_geojson(shape),
		"h3_cells": json.dumps(h3_cells(geometry), separators=(",", ":")),
		"area_computed_acres": area_acres(geometry),
	}


def stored_cells(text) -> dict:
	"""The `h3_cells` JSON back as `{resolution: set(cells)}`, forgivingly."""
	try:
		payload = json.loads(str(text or "") or "{}")
	except json.JSONDecodeError:
		return {}
	return {
		str(resolution): set(cells)
		for resolution, cells in (payload or {}).items()
		if isinstance(cells, list)
	}


def stored_shape(text):
	"""A shapely geometry from a stored boundary string, or None if unreadable."""
	if not text or not HAVE_SHAPELY:
		return None
	try:
		return shapely_shape(json.loads(str(text)))
	except Exception:
		return None
