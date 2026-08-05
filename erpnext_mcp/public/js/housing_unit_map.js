// SPDX-License-Identifier: MIT
/**
 * Where the cabin actually stands.
 *
 * v0.32.0. A camp address is a driveway off a county road and a cabin number is
 * paint on a door; neither puts an ambulance, an inspector or a crew bus at the
 * right building. The parcel outline is drawn underneath when it has one, which
 * is what makes a coordinate somebody keyed in wrongly visible at a glance —
 * a marker outside its own parcel is not a subtle error once it is on a map.
 */

frappe.ui.form.on("Housing Unit", {
	refresh(frm) {
		const point = erpnext_mcp.geo_map.point_of(frm.doc.gps_latitude, frm.doc.gps_longitude);
		if (!point) {
			return;
		}
		erpnext_mcp.geo_map
			.fetch_boundary("Parcel", frm.doc.parcel, frm.doc.parcel, "#9a6700")
			.then((parcel) => {
				erpnext_mcp.geo_map.render(frm, {
					title: __("Location"),
					geometries: parcel ? [parcel] : [],
					point: point,
					point_label: `${frm.doc.unit_name || frm.doc.name} — ${point[0]}, ${point[1]}`,
				});
			});
	},
});
