// SPDX-License-Identifier: MIT
/**
 * The parcel outline — the outer shape, the one the deed and the tax bill both
 * describe.
 *
 * v0.32.0. No container above it: a parcel is the top of this app's geography.
 * What sits inside it is drawn on each block's and each zone's own form, and
 * `set_parcel_boundary` names anything that falls outside.
 */

erpnext_mcp.geo_map.attach_boundary("Parcel", {
	title: __("Parcel Boundary"),
	colour: "#9a6700",
});
