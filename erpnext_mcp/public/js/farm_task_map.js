// SPDX-License-Identifier: MIT
/**
 * Where the work is, resolved through whatever register names the place.
 *
 * v0.32.0. A Farm Task does not hold a coordinate of its own and should not: the
 * place already exists as a Parcel, a Field, an Irrigation Zone or a Housing
 * Unit, and copying its position onto the task would be a second copy to go
 * stale the first time a boundary is corrected. So this reads the position from
 * whichever record `location_doctype` names — a polygon for the three that carry
 * one, a marker for a cabin — and draws that.
 *
 * A TASK WITH NO LOCATION GETS NO MAP AND THAT IS CORRECT. A certificate renewal
 * happens at a desk. Only a task that names a place has a place to show.
 */

// WRAPPED IN AN IIFE so nothing here declares a global. A `doctype_js` file is
// evaluated by appending a <script> to the page, and a top-level `const` in a
// classic script is a global lexical binding — a second evaluation of the same
// file would be a SyntaxError that takes the whole form script down, and the
// symptom is a form with no map and no explanation.

(function () {

	//: Which register answers "where" with a shape, and which with a point.
	const POLYGON_LOCATIONS = ["Parcel", "Field", "Irrigation Zone"];
	const POINT_LOCATIONS = { "Housing Unit": ["gps_latitude", "gps_longitude"] };

	frappe.ui.form.on("Farm Task", {
		refresh(frm) {
			const doctype = frm.doc.location_doctype;
			const name = frm.doc.location;
			if (!doctype || !name) {
				return;
			}
			if (POLYGON_LOCATIONS.indexOf(doctype) !== -1) {
				erpnext_mcp.geo_map
					.fetch_boundary(doctype, name, `${doctype} ${name}`, "#1f6feb")
					.then((entry) => {
						if (!entry) {
							return;
						}
						entry.fill_opacity = 0.12;
						erpnext_mcp.geo_map.render(frm, {
							title: __("Where the Work Is"),
							geometries: [entry],
						});
					});
				return;
			}
			const fields = POINT_LOCATIONS[doctype];
			if (!fields) {
				return;
			}
			frappe.db
				.get_value(doctype, name, fields)
				.then((response) => {
					const row = (response && response.message) || {};
					const point = erpnext_mcp.geo_map.point_of(row[fields[0]], row[fields[1]]);
					if (!point) {
						return;
					}
					erpnext_mcp.geo_map.render(frm, {
						title: __("Where the Work Is"),
						point: point,
						point_label: `${doctype} ${name}`,
					});
				})
				.catch(() => {});
		},
	});
})();
