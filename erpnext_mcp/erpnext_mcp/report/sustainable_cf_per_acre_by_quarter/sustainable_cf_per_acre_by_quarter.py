# SPDX-License-Identifier: MIT
"""Sustainable CF/Acre, one row per quarter, for the dashboard chart to draw.

A SCRIPT REPORT AND NOT A QUERY REPORT, because there is no SQL that produces
this. The KPI is a normalized figure: it needs the approved adjustments summed
with their directions applied, the maintenance portion of each classified asset,
and a denominator weighted by how many days of the quarter each block was
actually productive. A query that tried would be a reimplementation of
`services/sustainable_cf_per_acre.py` in SQL, and the two would disagree within a
release — which is precisely the failure the service exists in one place to
prevent.

So this calls `compute` once per quarter and lays the answers out flat.

WHY A REPORT AND NOT FRAPPE INSIGHTS. Insights would draw this more handsomely
and is a separate app with its own install, its own permissions model and its own
upgrade path; a KPI that only renders on sites which happen to have it is a KPI
half the operation cannot see. Report plus Dashboard Chart are core Frappe, work
on every site this app supports, and honour the site's own permissions. An
Insights view over the same service is a later release and loses nothing by
waiting — noted in RELEASES/v0.19.5.md.

THE COMPONENTS TRAVEL WITH THE FIGURE, in columns rather than as a tooltip.
Normalized OCF, maintenance capex and the time-weighted acres are each their own
column, because the interesting question about a quarter where the number fell is
always WHICH OF THE THREE MOVED — and a chart of the ratio alone cannot answer
it. The unclassified-asset count rides along for the same reason: a quarter whose
capex was never classified reports a flatteringly high figure, and the column is
what says so on the face of the report.
"""

import frappe
from frappe import _

from erpnext_mcp.services import sustainable_cf_per_acre as service

#: Quarter boundaries as (label, start month/day, end month/day). Calendar
#: quarters rather than fiscal ones, and deliberately: the chart is read beside a
#: lender's own quarters, and a fiscal year that starts in July would otherwise
#: produce a "Q1" nobody outside the operation can line up with anything.
QUARTERS = (
	("Q1", (1, 1), (3, 31)),
	("Q2", (4, 1), (6, 30)),
	("Q3", (7, 1), (9, 30)),
	("Q4", (10, 1), (12, 31)),
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	company = filters.get("company")
	year = int(filters.get("year") or str(frappe.utils.today())[:4])

	if not company:
		companies = frappe.db.get_all("Company", pluck="name", limit=2)
		if len(companies) == 1:
			company = companies[0]
	if not company:
		# Not an error: a multi-company site with no filter chosen yet. An empty
		# report with its columns is a page somebody can pick a company on; an
		# exception is a page they cannot.
		return _columns(), []

	rows = []
	for label, (start_month, start_day), (end_month, end_day) in QUARTERS:
		period_start = f"{year}-{start_month:02d}-{start_day:02d}"
		period_end = f"{year}-{end_month:02d}-{end_day:02d}"
		report = service.compute(company, period_start, period_end)
		rows.append(
			{
				"quarter": f"{year} {label}",
				"company": company,
				"period_start": period_start,
				"period_end": period_end,
				"normalized_ocf": report["normalized_ocf"],
				"maintenance_capex": report["maintenance_capex"]["total"],
				"productive_acres": report["productive_acres"]["time_weighted"],
				"sustainable_cf_per_acre": report["sustainable_cf_per_acre"],
				"adjustments": len(report["normalization_adjustments"]),
				"unclassified_assets": report["maintenance_capex"]["unclassified_asset_count"],
				"warnings": len(report["computation_warnings"]),
			}
		)
	return _columns(), rows


def _columns():
	return [
		{"fieldname": "quarter", "label": _("Quarter"), "fieldtype": "Data", "width": 110},
		{
			"fieldname": "sustainable_cf_per_acre",
			"label": _("Sustainable CF/Acre"),
			"fieldtype": "Currency",
			"width": 170,
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
		{"fieldname": "warnings", "label": _("Warnings"), "fieldtype": "Int", "width": 100},
		{
			"fieldname": "company",
			"label": _("Company"),
			"fieldtype": "Link",
			"options": "Company",
			"width": 180,
		},
	]
