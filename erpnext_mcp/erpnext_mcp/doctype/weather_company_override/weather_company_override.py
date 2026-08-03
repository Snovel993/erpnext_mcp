# SPDX-License-Identifier: MIT
"""Controller for Weather Company Override — a child table, and empty on purpose.

Frappe imports one module per DocType, child tables included, and a folder with a
JSON and no module breaks `bench migrate` rather than degrading. See
`farm_task_evidence.py` for the same note.

THE ONE RULE THIS TABLE HAS IS ON THE PARENT, not here — `Weather Settings`
refuses two rows for one company, because the check needs every row at once and a
child controller only ever sees itself. `services/weather.thresholds_for` reads
the first row it finds either way; the refusal is what stops there being a second
one to find.
"""

from frappe.model.document import Document


class WeatherCompanyOverride(Document):
	pass
