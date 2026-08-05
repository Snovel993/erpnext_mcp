// SPDX-License-Identifier: MIT
/**
 * The zone's own boundary, drawn inside the block it waters.
 *
 * v0.32.0. Containment here is reported and deliberately not enforced — a shared
 * water line crosses a boundary, a pump house sits on the headland, a mainline
 * runs down a road easement — so the map is where somebody decides which of
 * those a given overhang is.
 */

erpnext_mcp.geo_map.attach_boundary("Irrigation Zone", {
	title: __("Zone Boundary"),
	container: { doctype: "Field", field: "field" },
	colour: "#1a7f37",
});
