# SPDX-License-Identifier: MIT
"""Controller for Heat Acclimatization Worker — a child table, and empty on purpose.

Frappe imports one module per DocType, child tables included, and a folder with a
JSON and no module breaks `bench migrate` rather than degrading. See
`farm_task_evidence.py` for the same note.

WHETHER SOMEBODY NEEDS AN ACCLIMATIZATION PLAN IS NOT A FACT ABOUT THIS ROW. It
is a fact about how many days that person has worked in the heat, which lives on
their Employee record and their attendance history, and about which shift is
being documented. So the checking — that the worker was actually on the crew —
lives in `tools/heat.py`, where the shift and the plan are both in hand.

There is nothing true of one of these rows on its own.
"""

from frappe.model.document import Document


class HeatAcclimatizationWorker(Document):
	pass
