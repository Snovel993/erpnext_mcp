// SPDX-License-Identifier: MIT
/**
 * The parcel outline — the outer shape, the one the deed and the tax bill both
 * describe.
 *
 * v0.32.0. No container above it: a parcel is the top of this app's geography.
 * What sits inside it is drawn on each block's and each zone's own form, and
 * `set_parcel_boundary` names anything that falls outside.
 *
 * v0.33.0 MAKES IT DRAWABLE, AND ADDS THE COUNTY IMPORT — which is on this form
 * and on no other. A county publishes TAX LOTS, and a tax lot is the unit the
 * assessor, the deed and the tax bill all agree on, which is exactly what a
 * Parcel is. It has never heard of an orchard block or an irrigation zone, so an
 * import button on those forms would be an invitation to set a block's boundary
 * to the whole lot it happens to sit in.
 *
 * AND THE COUNTY'S POLYGON IS BETTER THAN A TRACED ONE for this shape
 * specifically. A block boundary is a farming decision that only the farm knows;
 * a parcel boundary is a legal fact that somebody has already surveyed, recorded
 * and published. Tracing that by eye off a satellite image — which is what
 * everybody does when importing is hard — reproduces a known answer badly.
 */

erpnext_mcp.geo_map.attach_editable_boundary("Parcel", {
	title: __("Parcel Boundary"),
	county: true,
});
