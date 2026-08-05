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
 *
 * v0.33.0 MAKES IT DRAWABLE, and the parcel underneath is what makes drawing it
 * possible at all: a block corner on a satellite image is a change in canopy, and
 * the deed line it should stop at is not visible from the air. Both on one map,
 * one of them editable, is the only arrangement in which somebody can trace a
 * block and see where it lands.
 *
 * NO COUNTY IMPORT HERE. The county knows tax lots, not blocks — see
 * `parcel_map.js`.
 */

erpnext_mcp.geo_map.attach_editable_boundary("Field", {
	title: __("Block Boundary"),
	container: { doctype: "Parcel", field: "parcel" },
});
