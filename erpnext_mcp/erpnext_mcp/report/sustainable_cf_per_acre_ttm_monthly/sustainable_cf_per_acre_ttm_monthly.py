# SPDX-License-Identifier: MIT
"""Sustainable CF/Acre, one ROLLING TWELVE MONTHS per month, for two years.

v0.19.6, and the default view of the KPI from here on. The quarterly report
beside it is not replaced and not wrong; it answers a different question and
says so.

────────────────────────────────────────────────────────────────────────────
WHY THE ROLLING LINE IS THE DEFAULT AND THE QUARTERLY BARS ARE NOT
────────────────────────────────────────────────────────────────────────────

`sustainable_cf_per_acre_by_quarter` draws four discrete calendar quarters, and
on a farm those four points are not comparable with each other. Q3 is harvest
and Q1 is pruning; the line falls off a cliff every January and climbs back
every September, on every operation, in every year, whether or not anything
happened. A reader who does not already know that reads a crisis; a reader who
does knows the chart tells them nothing.

Each point HERE is a full twelve months — the month it is labelled with, plus
the eleven before it. Pruning, thinning, harvest and the winter are inside every
single point exactly once, so consecutive points differ only by the month that
entered and the month that left. THE LINE MOVES WHEN THE BUSINESS MOVES, which
is the only property that makes a trend worth drawing.

TWENTY-FOUR POINTS, which is two years and is chosen rather than inherited.
Twelve would show a rolling figure whose first and last points share no month at
all and would read as a trend with nothing to compare it to; sixty would be a
five-year chart at monthly resolution that nobody can see the recent movement
in. Two years is where a deferred-maintenance drift becomes visible while the
current year is still legible.

────────────────────────────────────────────────────────────────────────────
THE MEAN IS A `yMarker` AND THAT IS WHY IT IS DASHED
────────────────────────────────────────────────────────────────────────────

The whole point of the overlay is "above or below normal, at a glance", and a
second solid line invites the reader to compare its SHAPE with the first — which
is meaningless, because it has none. frappe-charts renders a `yMarker` as a
dashed horizontal rule with a label, which is exactly what a reference level is
and exactly how every chart that has ever drawn one draws it. It is the native
idiom rather than a line pretending to be a rule.

The value is the mean of the prior TTM series across the visible range, taken
from the same `windowed_reports` machinery the tool uses — so the chart and
`get_windowed_report` can never disagree about what "average" means.

────────────────────────────────────────────────────────────────────────────
THE COMPONENTS ARE COLUMNS, NOT A TOOLTIP, AND THAT IS DELIBERATE
────────────────────────────────────────────────────────────────────────────

The interesting question about a month where the rolling figure fell is always
WHICH OF THE THREE MOVED — normalized OCF, maintenance capex, or the acres — and
the ratio alone cannot answer it. So each is its own column, exactly as the
quarterly report does it.

They are NOT in the chart tooltip, and that is a choice rather than an
oversight. A frappe-charts tooltip shows the datasets it was given; putting four
component figures in one would mean either four more datasets on a chart that is
about one number, or a custom tooltip formatter this app would then own across
every Frappe version it supports. The columns are where a reader actually
inspects a figure — they sort, they export, and they are already how the
quarterly report presents the same three ingredients.

────────────────────────────────────────────────────────────────────────────
IT READS THE CACHE
────────────────────────────────────────────────────────────────────────────

Twenty-four rolling windows is twenty-four full computations over twelve months
of GL each, which is not a thing to do while somebody watches a dashboard load.
`windowed_reports.run` answers from `Financial KPI History` where the overnight
sweep has been, and computes only what is missing — so the second open of the
dashboard is instant and the first is bounded.
"""

import frappe
from frappe import _

from erpnext_mcp.services import financial_reports  # noqa: F401  (registers the computers)
from erpnext_mcp.services import windowed_reports as windows

#: Months of rolling history the chart draws. See the module docstring: two
#: years is where a maintenance drift becomes visible while the current season
#: is still legible.
VISIBLE_MONTHS = 24

REPORT_LABEL = "Sustainable CF/Acre TTM Monthly"


def execute(filters=None):
	filters = frappe._dict(filters or {})
	company = filters.get("company")
	if not company:
		companies = frappe.db.get_all("Company", pluck="name", limit=2)
		if len(companies) == 1:
			company = companies[0]
	if not company:
		# Not an error: a multi-company site with no filter chosen yet. An empty
		# report with its columns is a page somebody can pick a company on; an
		# exception is a page they cannot.
		return _columns(), [], None, _chart([], None)

	as_of = str(filters.get("as_of") or frappe.utils.today())
	months = _visible_months(filters)

	report = windows.run(
		"sustainable_cf_per_acre",
		company,
		as_of=as_of,
		window_type=windows.WINDOW_TTM,
		window_months=12,
		computation_step=windows.STEP_MONTHLY,
		historical_lookback_years=_lookback_for(months),
	)

	rows = _rows(report, company, months)
	mean = (report.get("historical_averages") or {}).get("prior_ttm_mean")
	message = _message(report, mean)
	return _columns(), rows, message, _chart(rows, mean)


def _visible_months(filters) -> int:
	try:
		return max(2, min(60, int(filters.get("months") or VISIBLE_MONTHS)))
	except (TypeError, ValueError):
		return VISIBLE_MONTHS


def _lookback_for(months: int) -> int:
	"""Years of history to ask for so the chart has `months` points to draw.

	Rounded UP, because a chart short of its last point is a chart that stops
	before the thing somebody opened it to see.
	"""
	return max(1, min(windows.MAX_LOOKBACK_YEARS, -(-months // 12)))


def _rows(report: dict, company: str, months: int) -> list:
	"""The current window plus the prior series, newest last so the line reads left to right."""
	window = report.get("window") or {}
	series = list((report.get("historical_averages") or {}).get("prior_ttm_series") or [])

	points = []
	if window.get("period_end"):
		points.append(
			{
				"as_of": window["period_end"],
				"period_start": window.get("period_start"),
				"period_end": window.get("period_end"),
				"value": window.get("value"),
				"components": window.get("components") or {},
			}
		)
	for entry in series[: max(0, months - 1)]:
		points.append(
			{
				"as_of": entry.get("as_of"),
				"period_start": entry.get("period_start"),
				"period_end": entry.get("period_end"),
				"value": entry.get("value"),
				# The prior entries carry the figure and not the ingredients: the
				# series is built for the averages, and pulling sixty components
				# dicts through to fill columns nobody sorted on would be a great
				# deal of JSON for a chart that draws one number per point.
				"components": {},
			}
		)
	points.reverse()

	rows = []
	for point in points:
		components = point["components"]
		capex = components.get("maintenance_capex") or {}
		acres = components.get("productive_acres") or {}
		rows.append(
			{
				"month": str(point["as_of"] or "")[:7],
				"as_of": point["as_of"],
				"company": company,
				"period_start": point["period_start"],
				"period_end": point["period_end"],
				"sustainable_cf_per_acre": point["value"],
				"normalized_ocf": components.get("normalized_ocf"),
				"maintenance_capex": capex.get("total"),
				"productive_acres": acres.get("time_weighted"),
				"adjustments": len(components.get("normalization_adjustments") or []) or None,
				"unclassified_assets": capex.get("unclassified_asset_count"),
			}
		)
	return rows


def _message(report: dict, mean) -> str:
	"""What a reader has to know before believing the line, above the line.

	The warnings are the same sentences `get_windowed_report` returns, rendered
	here rather than summarised — a chart with a partial window in it looks
	exactly like a chart with a full one, and the difference is the whole claim.
	"""
	warnings = report.get("computation_warnings") or []
	averages = report.get("historical_averages") or {}
	parts = []
	if mean is not None:
		parts.append(
			_("The dashed rule is the mean of {0} prior rolling windows: {1}.").format(
				averages.get("prior_ttm_count"), mean
			)
		)
	else:
		parts.append(
			_(
				"There is no prior-window mean to draw yet — this operation has less than two "
				"rolling windows of ledger. The line is still the truth about the months it covers."
			)
		)
	if warnings:
		parts.append("<br><br>" + "<br>".join(frappe.utils.escape_html(line) for line in warnings))
	return "".join(parts)


def _chart(rows: list, mean) -> dict:
	"""One line and one dashed reference rule. See the module docstring on why a
	`yMarker` rather than a second dataset."""
	chart = {
		"data": {
			"labels": [row["month"] for row in rows],
			"datasets": [
				{
					"name": _("Sustainable CF/Acre (TTM)"),
					"values": [row["sustainable_cf_per_acre"] or 0 for row in rows],
				}
			],
		},
		"type": "line",
		"lineOptions": {"regionFill": 0, "hideDots": 0},
		"axisOptions": {"xIsSeries": 1},
	}
	if mean is not None:
		chart["yMarkers"] = [{"label": _("Prior TTM mean"), "value": mean, "options": {"labelPos": "left"}}]
	return chart


def _columns():
	return [
		{"fieldname": "month", "label": _("TTM Ending"), "fieldtype": "Data", "width": 110},
		{
			"fieldname": "sustainable_cf_per_acre",
			"label": _("Sustainable CF/Acre (TTM)"),
			"fieldtype": "Currency",
			"width": 200,
		},
		{
			"fieldname": "normalized_ocf",
			"label": _("Normalized OCF"),
			"fieldtype": "Currency",
			"width": 150,
		},
		{
			"fieldname": "maintenance_capex",
			"label": _("Maintenance Capex"),
			"fieldtype": "Currency",
			"width": 160,
		},
		{
			"fieldname": "productive_acres",
			"label": _("Productive Acres"),
			"fieldtype": "Float",
			"precision": 2,
			"width": 140,
		},
		{"fieldname": "adjustments", "label": _("Adjustments"), "fieldtype": "Int", "width": 110},
		{
			"fieldname": "unclassified_assets",
			"label": _("Unclassified Assets"),
			"fieldtype": "Int",
			"width": 160,
		},
		{"fieldname": "period_start", "label": _("Window Start"), "fieldtype": "Date", "width": 120},
		{"fieldname": "period_end", "label": _("Window End"), "fieldtype": "Date", "width": 120},
		{
			"fieldname": "company",
			"label": _("Company"),
			"fieldtype": "Link",
			"options": "Company",
			"width": 180,
		},
	]
