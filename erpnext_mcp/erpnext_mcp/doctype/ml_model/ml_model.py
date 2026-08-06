# SPDX-License-Identifier: MIT
"""Controller for ML Model — validates the record and enforces the one invariant.

VALIDATION REUSES `model_registry.validate_model_registration` RATHER THAN
DUPLICATING IT, unlike Budget's controller: a Budget's duplicate-row checks are
genuinely doctype-shaped (child table rows), where an ML Model's checks are the
same plain-dict checks `register_model`/`update_model` already run before ever
building the document — running them again here means a record edited straight
in the Desk gets the same guarantees a tool call does.

THE ONE INVARIANT THIS CONTROLLER OWNS THAT THE TOOL LAYER DOES NOT: at most
one Active ML Model per (company, piecework_activity), enforced here rather
than only in `activate_model`, so it holds regardless of which door a save came
through — the MCP tool, the Desk, or a data import. `activate_model` still does
the work of REPORTING what it superseded (`model_registry.check_model_conflicts`
gives it that before saving); this just makes sure the DATABASE never disagrees
with that report, even when nothing called `activate_model` at all.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp import model_registry, shifts

DOCTYPE = "ML Model"


class MLModel(Document):
	def autoname(self):
		"""MLM-YYYY-0001, where YYYY is the year the model was registered."""
		year = str(frappe.utils.today())[:4]
		self.name = shifts.next_in_series(DOCTYPE, "MLM", year)

	def validate(self):
		self._run_pure_validation()
		self._deprecate_superseded_siblings()

	def _run_pure_validation(self) -> None:
		errors = model_registry.validate_model_registration(self.as_dict())
		if errors:
			frappe.throw(_("{0} Nothing was saved.").format("; ".join(errors)))

	def _deprecate_superseded_siblings(self) -> None:
		"""If this save leaves the record Active, every OTHER Active model for the
		same (company, piecework_activity) becomes Deprecated.

		Runs on every save where status is Active, not only ones that arrived
		through `activate_model` — see the module docstring on why the invariant
		lives here rather than only in the tool. `frappe.db.set_value` rather
		than loading and saving each sibling: this is a status flip during
		another document's own validate, and a second full `.save()` cycle here
		would re-enter every sibling's own validate for a change it did not ask
		for.
		"""
		if self.status != model_registry.STATUS_ACTIVE:
			return
		siblings = frappe.db.get_all(
			DOCTYPE,
			filters={
				"company": self.company,
				"piecework_activity": self.piecework_activity,
				"status": model_registry.STATUS_ACTIVE,
				"name": ("!=", self.name or ""),
			},
			pluck="name",
		)
		for sibling in siblings:
			frappe.db.set_value(DOCTYPE, sibling, "status", model_registry.STATUS_DEPRECATED)
