// SPDX-License-Identifier: MIT
/**
 * Where the tagged asset was last recorded as standing.
 *
 * v0.32.0. Asset Register has carried gps_latitude and gps_longitude since
 * v0.17.0 and nothing has ever drawn them. A pump, a bin trailer or a generator
 * is findable by coordinate and by nothing else — "the shop yard" is four acres.
 */

erpnext_mcp.geo_map.attach_point("Asset Register", { title: __("Asset Location") });
