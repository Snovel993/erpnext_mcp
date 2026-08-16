# SPDX-License-Identifier: MIT
"""Controller for Wizard Step — one screen of a flow.

`WizardDefinition.validate` is what ENFORCES these, for the reason its own docstring gives: a child row's `validate` does not run on a parent save.
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document


class WizardStep(Document):
	def validate(self):
		self.step_key = str(self.step_key or "").strip()
		if not self.step_key:
			frappe.throw(_("A wizard step needs a key — it is what `next_step` points at."))
		if not str(self.title_en or "").strip():
			frappe.throw(
				_("Every step needs an English title, which is what a missing translation falls back to.")
			)
		if self.visible_if:
			try:
				json.loads(self.visible_if) if isinstance(self.visible_if, str) else self.visible_if
			except (json.JSONDecodeError, ValueError, TypeError):
				frappe.throw(_("Visible If on step {0} is not valid JSON.").format(self.step_key))
