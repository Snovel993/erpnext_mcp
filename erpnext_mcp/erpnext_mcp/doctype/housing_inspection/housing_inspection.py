# SPDX-License-Identifier: MIT
"""Controller for Housing Inspection — the annual walk, and what it found.

THE STATE IS A FUNCTION OF THE FINDINGS. See `erpnext_mcp/records.py` for the
argument; the short version is that a worker who has written "water stain, north
wall, spreading" is not offered the option of marking the walk as passed, because
the state is computed from the text on every save rather than chosen.

THE WRITE-BACK HAPPENS HERE, NOT IN THE TOOL. Recording an inspection moves the
unit's `last_habitability_inspection` forward, which is the entire mechanism by
which doing the work takes `housing_inspection_overdue` off the calendar. Putting
it in the controller rather than in `tools/inspections.py` means a record typed
into the Desk by a camp manager who has never heard of MCP updates the register
exactly as one written by a tool does. A compliance system where the evidence and
the register agree only when the right door was used disagrees with itself by
August.

IT ONLY EVER MOVES THE DATE FORWARD. March's walk entered in July is filed as
evidence and does NOT move a register that already knows about June — that would
re-raise an alert about work which has since been done.

WHAT IT REFUSES IS SMALL. A unit that is not on the register, an inspection dated
in the future, and a closure dated before the inspection it closes. It does NOT
refuse an inspection of an Uninhabitable unit, or one whose findings are
alarming, or one entered four years late: every one of those is a fact worth
recording, and a controller that refused to record it would guarantee the record
stayed empty.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp.records import COMPLETE_STATES, advance_date, branch_state

#: The field on Housing Unit this record writes. Named here so the rule, the
#: tool and the controller cannot disagree about which date dismisses the alert.
UNIT_DATE_FIELD = "last_habitability_inspection"


class HousingInspection(Document):
	def autoname(self):
		month = str(self.inspection_date or frappe.utils.today())[:7]
		prefix = f"HI-{month}-"
		existing = frappe.db.get_all(
			"Housing Inspection",
			filters={"name": ("like", f"{prefix}%")},
			pluck="name",
			limit=100000,
		)
		highest = 0
		for name in existing or []:
			tail = str(name).rsplit("-", 1)[-1]
			if tail.isdigit():
				highest = max(highest, int(tail))
		self.name = f"{prefix}{highest + 1:05d}"

	def validate(self):
		if not self.unit:
			frappe.throw(
				_("Housing Unit is required — an inspection of nothing in particular is not evidence.")
			)
		if not self.inspection_date:
			frappe.throw(_("Inspection Date is required."))
		if str(self.inspection_date) > frappe.utils.today():
			frappe.throw(
				_("Inspection Date {0} is in the future. Nobody has walked it yet.").format(
					self.inspection_date
				)
			)

		self.inspector = str(self.inspector or "").strip()
		self.inspector_name = str(self.inspector_name or "").strip() or self.inspector
		self.company = self.company or frappe.db.get_value("Housing Unit", self.unit, "owning_entity")

		self.workflow_state = branch_state(self.findings, bool(int(self.keep_as_draft or 0)))

		if self.corrective_action_closed and str(self.corrective_action_closed) < str(self.inspection_date):
			frappe.throw(
				_(
					"Corrective Action Closed {0} is before the inspection on {1}. Nothing was "
					"fixed before it was found."
				).format(self.corrective_action_closed, self.inspection_date)
			)
		if self.corrective_action_closed and not str(self.closure_note or "").strip():
			frappe.throw(
				_(
					"Closing a finding needs a closure note saying what was actually done. A date "
					"with nothing beside it is what an auditor is trained to disbelieve."
				),
				title=_("Closure Note is required"),
			)

	def on_update(self):
		"""Move the unit's inspection date forward, once the record is not a Draft."""
		if self.workflow_state not in COMPLETE_STATES:
			return
		self.flags.register_write = advance_date(
			"Housing Unit", self.unit, UNIT_DATE_FIELD, self.inspection_date
		)


def open_corrective_action(row: dict) -> bool:
	"""Does this inspection still have a finding nobody has closed?

	Read off the row rather than the document, because the alert rule reads
	thousands of them and loading each one would be a query per cabin.
	"""
	from erpnext_mcp.records import CORRECTIVE_ACTION_REQUIRED

	if str(row.get("workflow_state") or "") != CORRECTIVE_ACTION_REQUIRED:
		return False
	return not str(row.get("corrective_action_closed") or "").strip()
