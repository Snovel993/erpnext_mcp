// SPDX-License-Identifier: MIT
/**
 * The zone's own boundary, drawn inside the block it waters.
 *
 * v0.32.0. Containment here is reported and deliberately not enforced — a shared
 * water line crosses a boundary, a pump house sits on the headland, a mainline
 * runs down a road easement — so the map is where somebody decides which of
 * those a given overhang is.
 *
 * v0.33.0 MAKES IT DRAWABLE, and this is the form where drawing beats every
 * other way of recording the shape. A zone is a valve and the rows it feeds:
 * there is no deed for it, no survey, and nothing to import from anywhere. Until
 * now it could only be described by typing coordinates, which is why almost none
 * of them have a boundary at all.
 *
 * NO COUNTY IMPORT HERE. The county knows tax lots, not valves — see
 * `parcel_map.js`.
 */

erpnext_mcp.geo_map.attach_editable_boundary("Irrigation Zone", {
	title: __("Zone Boundary"),
	container: { doctype: "Field", field: "field" },
});
