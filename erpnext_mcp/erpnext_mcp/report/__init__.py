# SPDX-License-Identifier: MIT
"""Standard reports this app ships — see each subdirectory for one.

Frappe imports `<module>/report/<name>/<name>.json` during the sync phase of
`bench migrate`, so a report here exists before any `after_migrate` hook runs.
That ordering is load-bearing for `dashboard.install_kpi_charts`, whose chart
names a report and would render an error rather than nothing if the row were
missing.
"""
