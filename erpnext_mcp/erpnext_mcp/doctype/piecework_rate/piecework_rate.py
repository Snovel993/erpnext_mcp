# SPDX-License-Identifier: MIT
"""Controller for Piecework Rate.

VALIDATION REUSES `wage_defaults.validate_piecework_rate` rather than restating
it, for the reason `ml_model.py` gives: the checks are plain-dict checks the MCP
tools already run before ever building a document, so running them here as well
means a row typed straight into the Desk gets the same guarantees a tool call
does.

THE ONE CHECK THAT LIVES HERE AND NOT IN THE PURE MODULE is the duplicate: at
most one row per (company, activity, effective_from), because it needs a
database read. Two rows starting on the same day for the same activity are not a
raise — they are two answers to one question, and `select_effective` would have
to break the tie on a docname. Refusing is better than resolving arbitrarily: a
rate that depends on which row was created first is a rate nobody can predict.
An ENDED row is exempt, so re-using a start date after closing a season's rate
is allowed.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp import wage_defaults

DOCTYPE = "Piecework Rate"


class PieceworkRate(Document):
	def validate(self):
		errors = wage_defaults.validate_piecework_rate(self.as_dict())
		if errors:
			frappe.throw(_("{0}. Nothing was saved.").format("; ".join(errors)))
		self._refuse_a_duplicate_start()

	def _refuse_a_duplicate_start(self) -> None:
		wanted = wage_defaults.normalize_activity(self.activity)
		siblings = frappe.db.get_all(
			DOCTYPE,
			filters={
				"company": self.company,
				"effective_from": self.effective_from,
				"is_active": 1,
				"name": ("!=", self.name or ""),
			},
			fields=["name", "activity"],
		)
		clash = next(
			(row for row in siblings if wage_defaults.normalize_activity(row.get("activity")) == wanted),
			None,
		)
		if clash:
			frappe.throw(
				_(
					"{0} already sets a {1} rate for {2} from {3}. Two active rows starting on "
					"the same day for the same activity is two answers to one question — edit "
					"that row, or close it and start this one on a later date. Nothing was saved."
				).format(clash["name"], wanted, self.company, self.effective_from)
			)
