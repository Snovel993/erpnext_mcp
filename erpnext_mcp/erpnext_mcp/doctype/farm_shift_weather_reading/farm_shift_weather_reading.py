# SPDX-License-Identifier: MIT
"""Controller for Farm Shift Weather Reading — a child table, and empty in v0.19.3.

Frappe imports one module per DocType, child tables included, and a folder with a
JSON and no module breaks `bench migrate` rather than degrading. See
`farm_task_evidence.py` for the same note.

THE TABLE SHIPS WITH NOTHING WRITING TO IT, AND THAT IS THE PLAN RATHER THAN AN
OVERSIGHT. Its shape is what `Farm Shift Compliance Event`'s snapshot columns
denormalise from and what `Heat Exposure Event`'s maxima will be computed over,
so fixing it now means v0.19.4 wires a fetch instead of migrating a schema under
live compliance records.

v0.19.4 ports `app/utils/weather.py` out of farm_app into
`erpnext_mcp/services/weather.py`, walks every shift with no `end_datetime` on a
fifteen-minute schedule, and backfills closed shifts from Open-Meteo's archive
API so a shift that ran before the service was switched on can still be
documented. Nothing about a reading is true on its own until then — a
`heat_index_f` is a fact about a place and a moment, and the place is on the
shift.
"""

from frappe.model.document import Document


class FarmShiftWeatherReading(Document):
	pass
