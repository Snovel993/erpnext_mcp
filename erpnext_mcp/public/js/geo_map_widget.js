// SPDX-License-Identifier: MIT
/**
 * A Leaflet map on a Desk form, for every doctype in this app that knows where it is.
 *
 * v0.32.0. WHY A MAP IS NOT DECORATION HERE. This app has been storing polygons
 * since v0.12.0 and coordinates since v0.17.0, and until now the only way to see
 * one was to read a Long Text field full of numbers. A boundary nobody can look
 * at is a boundary nobody checks, and the failure that produces is specific: a
 * block traced with two vertices transposed passes every validation this app
 * makes — it is a valid polygon, it is on Earth, its area is plausible — and it
 * is obviously wrong to anybody who sees it drawn. The map is the check that
 * catches the mistakes arithmetic cannot.
 *
 * ONE WIDGET, SEVEN FORMS. Everything below is shared; each doctype's own script
 * says only where its geometry lives. That is deliberate: seven copies of a
 * Leaflet bootstrap is seven places for the CDN URL, the tile attribution and
 * the zoom defaults to drift apart, and the first symptom of the drift is one
 * form that mysteriously has no map.
 *
 * IT IS LOADED FROM A CDN AND IT DEGRADES. Leaflet is not vendored into this app
 * and OpenStreetMap tiles are fetched from openstreetmap.org, so a bench with no
 * outbound internet — which is a perfectly reasonable way to run a farm's
 * server — gets no map. That case is HANDLED RATHER THAN LEFT TO FAIL: the
 * section renders a sentence saying the library could not be reached, and it
 * prints the coordinates in plain text underneath. The record is the coordinates;
 * the map is a reading of them, and losing the reading must not look like losing
 * the record.
 *
 * NOTHING HERE WRITES. The map is read-only in every direction — there is no
 * drag-to-move marker, no draw-a-polygon tool, and no save path of any kind. A
 * boundary is compliance evidence and it is set through `set_field_boundary`,
 * `set_zone_boundary` or `set_parcel_boundary`, which validate the shape, refuse
 * a self-intersection, compare the area against the recorded acreage and
 * recompute every derived field. A map that could nudge a vertex would be a way
 * to change all of that by accident, with no validation and no audit row.
 *
 * THE TILE LAYER IS OSM's PUBLIC SERVER, which is free and asks for
 * attribution rather than an API key. That attribution is in the layer options
 * below and must stay there: it is the condition of use, not a courtesy.
 */

frappe.provide("erpnext_mcp.geo_map");

(function () {
	if (erpnext_mcp.geo_map.render) {
		// Already installed by another doctype's form on this page. Every
		// `doctype_js` entry pulls this file in, so on a session that opens a
		// Field and then a Parcel it arrives twice.
		return;
	}

	const LEAFLET_VERSION = "1.9.4";
	const LEAFLET_JS = `https://cdnjs.cloudflare.com/ajax/libs/leaflet/${LEAFLET_VERSION}/leaflet.js`;
	const LEAFLET_CSS = `https://cdnjs.cloudflare.com/ajax/libs/leaflet/${LEAFLET_VERSION}/leaflet.css`;

	const TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
	const TILE_ATTRIBUTION =
		'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

	//: How far in a map opens on a single point. Seventeen is about a hundred
	//: metres across, which shows a cabin and the road to it — a point zoomed to
	//: nineteen shows a roof and no context, and a reader cannot tell one cabin
	//: from the next by its roof.
	const POINT_ZOOM = 17;

	//: The most a boundary is zoomed to when its shape is tiny. Without this a
	//: single-acre block fills the frame at street level and the reader cannot see
	//: which block it is next to.
	const MAX_FIT_ZOOM = 18;

	const MAP_HEIGHT = "360px";

	//: How long to wait for the CDN before giving up and printing the coordinates.
	//: Eight seconds is past any working connection and short enough that a bench
	//: with no internet does not look hung.
	const LOAD_TIMEOUT_MS = 8000;

	let loading = null;

	/** Leaflet, loaded once per page. Resolves with `L`, or rejects. */
	function load_leaflet() {
		if (window.L && window.L.map) {
			return Promise.resolve(window.L);
		}
		if (loading) {
			return loading;
		}
		loading = new Promise((resolve, reject) => {
			if (!document.querySelector(`link[href="${LEAFLET_CSS}"]`)) {
				const link = document.createElement("link");
				link.rel = "stylesheet";
				link.href = LEAFLET_CSS;
				document.head.appendChild(link);
			}
			const script = document.createElement("script");
			script.src = LEAFLET_JS;
			script.async = true;
			const timer = setTimeout(
				() => reject(new Error("timed out")),
				LOAD_TIMEOUT_MS
			);
			script.onload = () => {
				clearTimeout(timer);
				window.L && window.L.map ? resolve(window.L) : reject(new Error("loaded but empty"));
			};
			script.onerror = () => {
				clearTimeout(timer);
				reject(new Error("could not be fetched"));
			};
			document.head.appendChild(script);
		});
		return loading;
	}

	/** A GeoJSON geometry from a stored Long Text field, or null. */
	function parse_geometry(raw) {
		if (!raw || !String(raw).trim()) {
			return null;
		}
		try {
			const parsed = JSON.parse(raw);
			// Accept whatever the boundary tools accept, for the same reason they
			// do: a caller exporting from QGIS gets whichever of the three shapes
			// the export button produced.
			if (parsed && parsed.type === "FeatureCollection") {
				return (parsed.features || []).length === 1 ? parsed.features[0].geometry : parsed;
			}
			if (parsed && parsed.type === "Feature") {
				return parsed.geometry;
			}
			return parsed;
		} catch (error) {
			return null;
		}
	}

	/** `[lat, lon]` when both are real coordinates, else null.
	 *
	 * NULL ISLAND IS REFUSED. An unset Float pair reads as [0, 0], which is a
	 * real place in the Gulf of Guinea — and a map that flies there looks exactly
	 * like a map showing you where something is.
	 */
	function point_of(lat, lon) {
		const latitude = parseFloat(lat);
		const longitude = parseFloat(lon);
		if (!isFinite(latitude) || !isFinite(longitude)) {
			return null;
		}
		if (!latitude && !longitude) {
			return null;
		}
		if (latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180) {
			return null;
		}
		return [latitude, longitude];
	}

	/** "45.52,-122.68" or "Latitude 45.52, longitude -122.68" → `[lat, lon]`.
	 *
	 * The two spellings Farm Shift and Farm Task Assignment both accept, since
	 * v0.19.1. A place name comes back null and the caller says so — resolving it
	 * would mean geocoding, which is a network call to somebody's paid service to
	 * guess at a field name a farm made up.
	 */
	function parse_gps_string(raw) {
		const text = String(raw || "").trim();
		if (!text) {
			return null;
		}
		const numbers = text.match(/-?\d+(\.\d+)?/g);
		if (!numbers || numbers.length < 2) {
			return null;
		}
		return point_of(numbers[0], numbers[1]);
	}

	/** The colour a breadcrumb is drawn in, from its place in the day.
	 *
	 * Blue at the start of the shift through to red at the end. A single colour
	 * would draw a track that says where the crew went and not which way round
	 * they went, and the direction is half of what a reader is asking.
	 */
	function track_colour(fraction) {
		const hue = 220 - 220 * Math.max(0, Math.min(1, fraction));
		return `hsl(${Math.round(hue)}, 75%, 45%)`;
	}

	function escape_html(value) {
		return frappe.utils && frappe.utils.escape_html
			? frappe.utils.escape_html(String(value == null ? "" : value))
			: String(value == null ? "" : value).replace(/[&<>"']/g, (character) => {
					return {
						"&": "&amp;",
						"<": "&lt;",
						">": "&gt;",
						'"': "&quot;",
						"'": "&#39;",
					}[character];
				});
	}

	/** The section this map lives in, created once per form.
	 *
	 * `frm.dashboard.add_section` rather than an HTML field on the doctype: a
	 * field would mean editing seven shipped DocType JSONs to hold something that
	 * is presentation and not data, and it would put an empty grey box on every
	 * form of every unmapped record. A section is added only when there is
	 * something to draw.
	 */
	function section_for(frm, title) {
		const key = "__erpnext_mcp_map";
		if (frm[key] && frm[key].wrapper && document.body.contains(frm[key].wrapper)) {
			frm[key].body.innerHTML = "";
			return frm[key].body;
		}
		const wrapper = document.createElement("div");
		wrapper.className = "erpnext-mcp-map-section";
		const body = document.createElement("div");
		wrapper.appendChild(body);
		try {
			frm.dashboard.add_section($(wrapper), __(title || "Map"));
		} catch (error) {
			// A Frappe version whose dashboard does not take a label, or does not
			// have add_section at all. The map is worth more than the heading.
			try {
				frm.dashboard.add_section($(wrapper));
			} catch (inner) {
				frm.$wrapper.find(".form-layout").first().prepend(wrapper);
			}
		}
		frm[key] = { wrapper: wrapper, body: body };
		return body;
	}

	/** What to show when there is no Leaflet: the numbers, in plain text. */
	function render_fallback(body, reason, spec) {
		const lines = [];
		if (spec.point) {
			lines.push(`${spec.point[0]}, ${spec.point[1]}`);
		}
		(spec.geometries || []).forEach((entry) => {
			if (entry.centroid) {
				lines.push(`${entry.label || "centroid"}: ${entry.centroid[0]}, ${entry.centroid[1]}`);
			}
		});
		if (spec.track && spec.track.length) {
			lines.push(
				__("{0} location fix(es) recorded, from {1} to {2}", [
					spec.track.length,
					spec.track[0].timestamp || "?",
					spec.track[spec.track.length - 1].timestamp || "?",
				])
			);
		}
		body.innerHTML = `
			<div class="text-muted" style="padding:8px 0">
				<div>${escape_html(
					__("The map library could not be loaded ({0}), so the position is printed instead.", [
						reason,
					])
				)}</div>
				<div style="margin-top:6px;font-family:monospace">${lines
					.map((line) => escape_html(line))
					.join("<br>")}</div>
			</div>`;
	}

	/**
	 * Draw one map.
	 *
	 * `spec` carries whatever this record knows about where it is:
	 *   geometries: [{ geometry, centroid, label, colour }]  — polygons
	 *   point:      [lat, lon]                               — a single marker
	 *   point_label: string
	 *   track:      [{ lat, lon, timestamp, label }]         — a crew's breadcrumbs
	 *   title:      the section heading
	 * Any combination; a record with none of them gets no section at all.
	 */
	erpnext_mcp.geo_map.render = function (frm, spec) {
		spec = spec || {};
		const has_anything =
			(spec.geometries || []).length || spec.point || (spec.track || []).length;
		if (!has_anything) {
			return;
		}
		const body = section_for(frm, spec.title);
		const canvas = document.createElement("div");
		canvas.style.height = MAP_HEIGHT;
		canvas.style.width = "100%";
		canvas.style.borderRadius = "6px";
		body.appendChild(canvas);

		load_leaflet()
			.then((L) => {
				const map = L.map(canvas, { scrollWheelZoom: false });
				L.tileLayer(TILE_URL, { attribution: TILE_ATTRIBUTION, maxZoom: 19 }).addTo(map);
				const bounds = [];

				(spec.geometries || []).forEach((entry) => {
					if (!entry.geometry) {
						return;
					}
					const layer = L.geoJSON(entry.geometry, {
						style: {
							color: entry.colour || "#1f6feb",
							weight: 2,
							fillOpacity: entry.fill_opacity == null ? 0.12 : entry.fill_opacity,
						},
					}).addTo(map);
					if (entry.label) {
						layer.bindPopup(escape_html(entry.label));
					}
					try {
						const box = layer.getBounds();
						if (box.isValid()) {
							bounds.push(box.getSouthWest(), box.getNorthEast());
						}
					} catch (error) {
						// A geometry Leaflet drew but cannot bound. Nothing to fit to;
						// the other layers still decide the view.
					}
					if (entry.centroid) {
						L.circleMarker(entry.centroid, {
							radius: 5,
							color: entry.colour || "#1f6feb",
							fillColor: "#ffffff",
							fillOpacity: 1,
							weight: 2,
						})
							.addTo(map)
							.bindPopup(
								escape_html(
									__("Centroid: {0}, {1}", [entry.centroid[0], entry.centroid[1]])
								)
							);
						bounds.push(entry.centroid);
					}
				});

				if (spec.point) {
					L.marker(spec.point)
						.addTo(map)
						.bindPopup(
							escape_html(
								spec.point_label || `${spec.point[0]}, ${spec.point[1]}`
							)
						);
					bounds.push(spec.point);
				}

				const track = spec.track || [];
				if (track.length) {
					const line = track.map((fix) => [fix.lat, fix.lon]);
					L.polyline(line, { color: "#6e7781", weight: 3, opacity: 0.7 }).addTo(map);
					track.forEach((fix, index) => {
						const fraction = track.length > 1 ? index / (track.length - 1) : 0;
						L.circleMarker([fix.lat, fix.lon], {
							radius: index === 0 || index === track.length - 1 ? 6 : 4,
							color: track_colour(fraction),
							fillColor: track_colour(fraction),
							fillOpacity: 0.9,
							weight: 1,
						})
							.addTo(map)
							.bindPopup(escape_html(fix.label || fix.timestamp || ""));
					});
					line.forEach((position) => bounds.push(position));
				}

				if (bounds.length > 1) {
					map.fitBounds(L.latLngBounds(bounds).pad(0.08), { maxZoom: MAX_FIT_ZOOM });
				} else if (bounds.length === 1) {
					map.setView(bounds[0], POINT_ZOOM);
				}

				// Leaflet measures its container on creation, and the dashboard
				// section is still being laid out at that moment — without this the
				// map renders as a single tile in the top-left corner. It is the
				// oldest bug in embedding Leaflet and it looks like a broken map
				// rather than a layout problem, which is why it gets a comment.
				setTimeout(() => map.invalidateSize(), 120);
			})
			.catch((error) => {
				render_fallback(body, (error && error.message) || "unavailable", spec);
			});
	};

	/** One linked record's boundary, or a null entry. Never rejects.
	 *
	 * A parent whose polygon cannot be read must not stop the child's own map
	 * being drawn — the containing shape is context, and context is the part it
	 * is safe to lose.
	 */
	function fetch_boundary(doctype, name, label, colour) {
		if (!doctype || !name) {
			return Promise.resolve(null);
		}
		return frappe.db
			.get_value(doctype, name, [
				"boundary_geojson",
				"boundary_centroid_lat",
				"boundary_centroid_lon",
			])
			.then((response) => {
				const row = (response && response.message) || {};
				const geometry = parse_geometry(row.boundary_geojson);
				if (!geometry) {
					return null;
				}
				return {
					geometry: geometry,
					centroid: null,
					label: label || name,
					colour: colour || "#8250df",
					fill_opacity: 0.04,
				};
			})
			.catch(() => null);
	}

	/**
	 * The whole map for a doctype that carries a boundary: Field, Irrigation Zone
	 * and Parcel are the same form as far as this is concerned.
	 *
	 * `options.container` names the link field holding the shape this one is
	 * expected to sit inside — its parcel, or the block a zone waters. Drawing it
	 * underneath in a lighter outline is the point of the whole widget: the
	 * boundary tools REPORT containment and never enforce it, and a disagreement
	 * reported in a warning string is a disagreement nobody pictures. Drawn, it
	 * takes a second to see whether the overhang is the shared water line
	 * somebody meant or two vertices in the wrong order.
	 */
	erpnext_mcp.geo_map.attach_boundary = function (doctype, options) {
		options = options || {};
		frappe.ui.form.on(doctype, {
			refresh(frm) {
				const geometry = parse_geometry(frm.doc.boundary_geojson);
				const centroid = point_of(
					frm.doc.boundary_centroid_lat,
					frm.doc.boundary_centroid_lon
				);
				if (!geometry && !centroid) {
					return;
				}
				const own = {
					geometry: geometry,
					centroid: centroid,
					label: frm.doc.name,
					colour: options.colour || "#1f6feb",
				};
				const container = options.container ? frm.doc[options.container.field] : null;
				fetch_boundary(
					options.container && options.container.doctype,
					container,
					container,
					"#8250df"
				).then((outer) => {
					erpnext_mcp.geo_map.render(frm, {
						title: options.title || __("Boundary"),
						// The container FIRST, so the record's own shape is drawn on
						// top of it rather than underneath.
						geometries: outer ? [outer, own] : [own],
					});
				});
			},
		});
	};

	/** The whole map for a doctype that carries a single lat/lon pair. */
	erpnext_mcp.geo_map.attach_point = function (doctype, options) {
		options = options || {};
		const lat_field = options.lat_field || "gps_latitude";
		const lon_field = options.lon_field || "gps_longitude";
		frappe.ui.form.on(doctype, {
			refresh(frm) {
				const point = point_of(frm.doc[lat_field], frm.doc[lon_field]);
				if (!point) {
					return;
				}
				erpnext_mcp.geo_map.render(frm, {
					title: options.title || __("Location"),
					point: point,
					point_label: frm.doc.name,
				});
			},
		});
	};

	erpnext_mcp.geo_map.parse_geometry = parse_geometry;
	erpnext_mcp.geo_map.point_of = point_of;
	erpnext_mcp.geo_map.parse_gps_string = parse_gps_string;
	erpnext_mcp.geo_map.fetch_boundary = fetch_boundary;
})();
