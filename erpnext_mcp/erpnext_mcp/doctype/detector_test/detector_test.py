# SPDX-License-Identifier: MIT
"""Controller for Detector Test — the smoke and CO check on one camp building.

A FAILED TEST STILL WRITES THE DATE, AND THAT IS DELIBERATE. The stale-detector
alert asks one question — *does anybody know whether this detector works* — and a
test that came back Fail answers it. The answer is bad, so the record routes to
Corrective Action Required and raises a Critical alert of its own; but the
original alert is about ignorance, and the ignorance is over. Leaving the date
blank would keep the calendar saying "nobody has tested this" about a building
somebody tested this morning, which is the wrong sentence and hides the real one.

NOT PRESENT WRITES NO DATE, for the mirror reason. There is nothing to have
tested, so nothing about it is known, and `housing_detector_test_stale` should go
on saying so. It is also a finding in its own right: a building somebody sleeps
in with no CO detector is the most dangerous state this app records.

REPLACEMENT NEEDED IS SET FOR YOU when either detector failed or is absent, and
can be set by hand for one that passed but is at the end of its service life —
most domestic detectors are rated ten years and a unit that passes at eleven is
passing on borrowed time.

THE REPLACEMENT TASK IS RAISED BY THE TOOL, NOT HERE. The register write-back
belongs in the controller because it is a restatement of the evidence; raising a
Farm Task is an ACTION, and an action taken silently by a save hook is an action
nobody can predict — including one that would fire again on every subsequent save
of the same record. `create_detector_test` raises it, once, and records which one.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp.records import COMPLETE_STATES, advance_date, branch_state

PASS = "Pass"
FAIL = "Fail"
NOT_PRESENT = "Not Present"

#: Which detector writes which date on the unit. One table, so the controller,
#: the rule and the tool cannot disagree about what dismisses the alert.
DETECTORS = (
	("smoke_detector_result", "smoke_detector_last_test", "smoke"),
	("co_detector_result", "co_detector_last_test", "CO"),
)


class DetectorTest(Document):
	def autoname(self):
		month = str(self.test_date or frappe.utils.today())[:7]
		prefix = f"DT-{month}-"
		existing = frappe.db.get_all(
			"Detector Test",
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
			frappe.throw(_("Housing Unit is required."))
		if not self.test_date:
			frappe.throw(_("Test Date is required."))
		if str(self.test_date) > frappe.utils.today():
			frappe.throw(
				_("Test Date {0} is in the future. Nobody has pressed the button yet.").format(self.test_date)
			)

		self.smoke_detector_result = str(self.smoke_detector_result or PASS).strip() or PASS
		self.co_detector_result = str(self.co_detector_result or PASS).strip() or PASS
		for fieldname in ("smoke_detector_result", "co_detector_result"):
			if self.get(fieldname) not in (PASS, FAIL, NOT_PRESENT):
				frappe.throw(
					_("{0} must be one of: {1}, {2}, {3}.").format(fieldname, PASS, FAIL, NOT_PRESENT)
				)

		self.tester = str(self.tester or "").strip()
		self.tester_name = str(self.tester_name or "").strip() or self.tester
		self.company = self.company or frappe.db.get_value("Housing Unit", self.unit, "owning_entity")

		faults = self.faults()
		if faults:
			self.replacement_needed = 1
		self.workflow_state = branch_state(
			"; ".join(faults) or str(self.findings or "").strip() or ("replacement needed" if int(self.replacement_needed or 0) else ""),
			bool(int(self.keep_as_draft or 0)),
		)

		if self.corrective_action_closed and str(self.corrective_action_closed) < str(self.test_date):
			frappe.throw(
				_("Corrective Action Closed {0} is before the test on {1}.").format(
					self.corrective_action_closed, self.test_date
				)
			)
		if self.corrective_action_closed and not str(self.closure_note or "").strip():
			frappe.throw(
				_("Closing a detector fault needs a closure note saying what was fitted or repaired."),
				title=_("Closure Note is required"),
			)

	def faults(self) -> list:
		"""Every reason this test is not a clean one, in sentences."""
		out = []
		for fieldname, _date_field, label in DETECTORS:
			result = str(self.get(fieldname) or "")
			if result == FAIL:
				out.append(f"the {label} detector failed its test")
			elif result == NOT_PRESENT:
				out.append(f"there is no {label} detector in this building")
		return out

	def on_update(self):
		"""Move the unit's detector dates forward, for the detectors actually tested."""
		if self.workflow_state not in COMPLETE_STATES:
			return
		writes = []
		for fieldname, date_field, _label in DETECTORS:
			if str(self.get(fieldname) or "") == NOT_PRESENT:
				# Nothing was tested, so nothing is known, and the stale-detector
				# alert should go on saying so. See the module docstring.
				continue
			writes.append(advance_date("Housing Unit", self.unit, date_field, self.test_date))
		self.flags.register_writes = writes


def open_corrective_action(row: dict) -> bool:
	"""Does this test still have a fault nobody has closed?"""
	from erpnext_mcp.records import CORRECTIVE_ACTION_REQUIRED

	if str(row.get("workflow_state") or "") != CORRECTIVE_ACTION_REQUIRED:
		return False
	return not str(row.get("corrective_action_closed") or "").strip()
