// SPDX-License-Identifier: MIT
/**
 * The block's own boundary, drawn inside its parcel's.
 *
 * v0.32.0. Both shapes on one map because `set_field_boundary` REPORTS whether
 * the block sits inside its parcel and never enforces it — a planting that
 * predates a deed split really does straddle the line — and a warning string
 * about an overhang is something nobody pictures. Drawn, the difference between
 * "that is the corner we always farmed across" and "two vertices are in the
 * wrong order" takes a second to see.
 */

erpnext_mcp.geo_map.attach_boundary("Field", {
	title: __("Block Boundary"),
	container: { doctype: "Parcel", field: "parcel" },
});
