// SPDX-License-Identifier: MIT
/**
 * The shift's own place, and the crew's track over it.
 *
 * v0.32.0. THE TRACK IS THE POINT OF THIS ONE. A shift already carries a
 * location in the words the crew uses and a coordinate for the weather fetch,
 * and neither of those says where anybody went between six in the morning and
 * three in the afternoon. The breadcrumbs do, and drawn in time order they
 * answer two questions at once: which block the crew was in while a re-entry
 * interval was running, and whether the hours somebody is claiming were worked
 * where the work was.
 *
 * READ IN THE ORDER THE FIXES WERE TAKEN, which is why the query below orders by
 * `timestamp` and not by creation. A phone out of signal in a canyon posts an
 * hour of breadcrumbs the moment the bars come back; a track drawn in arrival
 * order shows the crew standing still all morning where the signal returned and
 * then teleporting across the farm.
 *
 * NOTHING IS INTERPOLATED. Where the phone was quiet the polyline runs straight
 * between the two fixes it does have, and that segment is a line the crew did
 * not necessarily walk — `get_shift_track` names every silence over ten minutes
 * for exactly this reason. Filling the gap in would put an invented position on
 * a record read in a wage dispute.
 */

// WRAPPED IN AN IIFE so nothing here declares a global. A `doctype_js` file is
// evaluated by appending a <script> to the page, and a top-level `const` in a
// classic script is a global lexical binding — a second evaluation of the same
// file would be a SyntaxError that takes the whole form script down, and the
// symptom is a form with no map and no explanation.

(function () {

	//: Most fixes drawn on one form. A nine-hour shift at a fix every thirty seconds
	//: is eleven hundred points and Leaflet draws them without complaint; past two
	//: thousand circle markers the form gets slow, and the shape of the day is
	//: already unmistakable well before then.
	const SHIFT_TRACK_LIMIT = 2000;

	frappe.ui.form.on("Farm Shift", {
		refresh(frm) {
			if (frm.is_new()) {
				return;
			}
			const anchor = erpnext_mcp.geo_map.parse_gps_string(frm.doc.farm_location_gps);
			frappe.db
				.get_list("Shift Location Log", {
					filters: { shift: frm.doc.name },
					fields: ["latitude", "longitude", "timestamp", "employee_name", "accuracy_meters"],
					order_by: "timestamp asc",
					limit: SHIFT_TRACK_LIMIT,
				})
				.then((rows) => {
					const track = [];
					(rows || []).forEach((row) => {
						const point = erpnext_mcp.geo_map.point_of(row.latitude, row.longitude);
						if (!point) {
							return;
						}
						const accuracy =
							row.accuracy_meters == null
								? ""
								: ` (±${Math.round(row.accuracy_meters)} m)`;
						track.push({
							lat: point[0],
							lon: point[1],
							timestamp: row.timestamp,
							label: `${row.timestamp || ""}${accuracy}${
								row.employee_name ? " — " + row.employee_name : ""
							}`,
						});
					});
					if (!anchor && !track.length) {
						return;
					}
					erpnext_mcp.geo_map.render(frm, {
						title: track.length
							? __("Crew Track ({0} fixes)", [track.length])
							: __("Shift Location"),
						point: anchor,
						point_label: frm.doc.location || frm.doc.name,
						track: track,
					});
				})
				.catch(() => {
					// The doctype may not be migrated yet, or this account may not read
					// it. Either way the shift's own anchor is still worth drawing —
					// losing the track must not lose the map.
					if (anchor) {
						erpnext_mcp.geo_map.render(frm, {
							title: __("Shift Location"),
							point: anchor,
							point_label: frm.doc.location || frm.doc.name,
						});
					}
				});
		},
	});
})();
