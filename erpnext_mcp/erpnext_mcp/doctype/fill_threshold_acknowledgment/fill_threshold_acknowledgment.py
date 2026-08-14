# SPDX-License-Identifier: MIT
"""Controller for Fill Threshold Acknowledgment — a child table, and empty on
purpose. Frappe imports one module per DocType, child tables included; a
folder with a JSON and no module breaks `bench migrate`.

Idempotency (an employee acknowledging the same version twice writes one row,
not two) lives in `tools/fill_pipeline.py::acknowledge_threshold_update`,
because deciding that needs to see every other row on the parent, which one
row cannot.
"""

from frappe.model.document import Document


class FillThresholdAcknowledgment(Document):
	pass
