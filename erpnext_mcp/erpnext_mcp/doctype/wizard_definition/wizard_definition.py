# SPDX-License-Identifier: MIT
"""Controller for Wizard Definition — one multi-step flow, as data.

THE DOCNAME IS THE KEY. A handset asks for `accident_investigation` and an
operator edits `accident_investigation`; those being the same row is what makes
"config not code" mean anything, and an autoname off `wizard_key` is what makes
it true.

WHAT IS CHECKED IS THE FLOW'S COHERENCE, because the failure mode of a wizard is
a worker stuck on a screen in an orchard with no way forward: a `next_step` that
points at nothing, two steps with the same key, two fields on one step under the
same name. All three are cheap to find here and expensive to find there.
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document


class WizardDefinition(Document):
	def validate(self):
		self.wizard_key = str(self.wizard_key or "").strip()
		if not self.wizard_key:
			frappe.throw(_("Wizard Key is required — it is the docname and what a handset asks for."))
		if not str(self.title_en or "").strip():
			frappe.throw(_("An English title is required; it is what a missing translation falls back to."))

		steps = list(self.get("steps") or [])
		if not steps:
			frappe.throw(_("A wizard with no steps is a screen a worker cannot get past. Add at least one."))

		keys = [str(step.get("step_key") or "").strip() for step in steps]
		duplicates = sorted({key for key in keys if keys.count(key) > 1 and key})
		if duplicates:
			frappe.throw(
				_("Two steps share the key(s) {0}. `next_step` could not tell them apart.").format(
					", ".join(duplicates)
				)
			)

		for step in steps:
			target = str(step.get("next_step") or "").strip()
			if target and target not in keys:
				frappe.throw(
					_(
						"Step {0} points next at {1}, which is not a step of this wizard. A worker "
						"reaching it would have nowhere to go."
					).format(step.get("step_key"), target)
				)
			if not str(step.get("title_en") or "").strip():
				frappe.throw(
					_(
						"Step {0} has no English title, which is what a missing translation falls back to."
					).format(step.get("step_key"))
				)
			self._check_json(step.get("visible_if"), f"Visible If on step {step.get('step_key')}")

			names = [str(field.get("fieldname") or "").strip() for field in (step.get("fields") or [])]
			clashing = sorted({name for name in names if names.count(name) > 1 and name})
			if clashing:
				frappe.throw(
					_("Step {0} has two fields called {1}; one answer would overwrite the other.").format(
						step.get("step_key"), ", ".join(clashing)
					)
				)

			# THE FIELD CHECKS RUN HERE AND NOT IN `WizardField.validate`, and the
			# reason is Frappe rather than taste: a child row's `validate` is not
			# called when its parent is saved, so a check that lived there would
			# be a check that never ran. The child controllers document the same
			# rules; this is where they are enforced.
			for field in step.get("fields") or []:
				fieldname = str(field.get("fieldname") or "").strip()
				if not fieldname:
					frappe.throw(
						_(
							"A field on step {0} has no fieldname — it is the key its answer arrives under."
						).format(step.get("step_key"))
					)
				if not str(field.get("label_en") or "").strip():
					frappe.throw(
						_(
							"Field {0} on step {1} has no English label. English is what a missing "
							"translation falls back to, so a field without one has nothing to serve "
							"a worker whose language this wizard has not been translated into."
						).format(fieldname, step.get("step_key"))
					)
				self._check_json(field.get("options"), f"Options on field {fieldname}")
				self._check_json(field.get("visible_if"), f"Visible If on field {fieldname}")

	def _check_json(self, raw, where: str) -> None:
		"""Refuse a blob that will not parse, at the cheap moment.

		A select whose options will not parse renders EMPTY on a handset in an
		orchard, and a worker standing in front of an empty dropdown has no way
		forward. Saving the wizard is where that should be found.
		"""
		if not raw:
			return
		try:
			json.loads(raw) if isinstance(raw, str) else raw
		except (json.JSONDecodeError, ValueError, TypeError):
			frappe.throw(_("{0} is not valid JSON.").format(where))
