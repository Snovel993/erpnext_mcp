# SPDX-License-Identifier: MIT
"""Controller for Wizard Field — one input on one step.

`WizardDefinition.validate` is what ENFORCES the rules below, because Frappe does not call a child row's `validate` when its parent is saved. They are restated here so somebody reading this file knows what a well-formed field is.

VALIDATION HERE IS ABOUT THE DEFINITION, NOT ABOUT THE ANSWER. A field whose
`options` will not parse as JSON is a select that renders empty on a handset in
an orchard, and the cheap place to find that out is when somebody saves the
wizard rather than when a worker opens it.
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document


class WizardField(Document):
	def validate(self):
		self.fieldname = str(self.fieldname or "").strip()
		if not self.fieldname:
			frappe.throw(_("A wizard field needs a fieldname — it is the key its answer arrives under."))
		if not str(self.label_en or "").strip():
			frappe.throw(
				_("Every field needs an English label, which is what a missing translation falls back to.")
			)
		for column in ("options", "visible_if"):
			raw = self.get(column)
			if not raw:
				continue
			try:
				json.loads(raw) if isinstance(raw, str) else raw
			except (json.JSONDecodeError, ValueError, TypeError):
				frappe.throw(
					_(
						"{0} on field {1} is not valid JSON. A select whose options will not parse "
						"renders empty on a handset in an orchard, and this is the cheap place to "
						"find that out."
					).format(column, self.fieldname)
				)
