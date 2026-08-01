# SPDX-License-Identifier: MIT
"""Controller for Water Test — one agricultural water sample, and what it said.

IT WRITES TWO REGISTERS, AND IT HAS TO. The sample came out of an Irrigation
Zone, so the zone's `water_test_last_date` is the honest place for it. But the
`water_test_stale` rule reads the BLOCK — because Subpart E is engaged by water
contacting a crop, and the crop is on the block — so a test filed only against
the zone would leave the calendar saying "untested" about ground whose water was
tested last week. Both are written, from the zone's own `field` link, and both
only ever move forward.

AN UNREADABLE RESULT IS NOT A CLEAN RESULT. `records.result_is_detection` reads
words first and numbers second, because a laboratory says the same thing eight
ways. Where it can read neither, the record routes to Corrective Action Required
rather than to Recorded: somebody has to go and look at the report. Treating an
uninterpretable result as a pass is exactly the failure that makes a compliance
file worthless — it is a clean record of nothing.

THE DATE THAT MATTERS IS THE SAMPLE'S, NOT THE LABORATORY'S. `test_date` is when
the water was in the pipe, which is what Subpart E's ninety days count back from.
`lab_reported_on` is recorded beside it because the gap between them is the
operation's real turnaround, and that gap is what makes "we will have the result
before we spray" either a plan or a hope.

DRAFT IS THE NORMAL FIRST STATE HERE, unlike the other two records. A sample is
taken on Monday and answered on Thursday; the record exists in between, holding
the chain of custody, with no result to branch on yet. It writes nothing to
either register until it has one.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp.records import (
	COMPLETE_STATES,
	ECOLI_ACTION_LEVEL_CFU,
	advance_date,
	branch_state,
	ecoli_over_action_level,
	result_is_detection,
)

#: Where the test date lands. The zone is where the sample came from; the block
#: is what the alert rule reads. Both, or the calendar disagrees with the lab.
ZONE_DATE_FIELD = "water_test_last_date"
BLOCK_DATE_FIELD = "water_test_last_date"


class WaterTest(Document):
	def autoname(self):
		month = str(self.test_date or frappe.utils.today())[:7]
		prefix = f"WT-{month}-"
		existing = frappe.db.get_all(
			"Water Test",
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
		if not self.source:
			frappe.throw(_("Irrigation Zone is required — a sample from nowhere in particular is not evidence."))
		if not self.test_date:
			frappe.throw(_("Test Date is required."))
		if str(self.test_date) > frappe.utils.today():
			frappe.throw(
				_("Test Date {0} is in the future. The sample has not been taken yet.").format(self.test_date)
			)
		if self.lab_reported_on and str(self.lab_reported_on) < str(self.test_date):
			frappe.throw(
				_("Lab Reported {0} is before the sample was taken on {1}.").format(
					self.lab_reported_on, self.test_date
				)
			)

		self.tester = str(self.tester or "").strip()
		self.tester_name = str(self.tester_name or "").strip() or self.tester

		zone = (
			frappe.db.get_value("Irrigation Zone", self.source, ["field", "owning_entity"], as_dict=True) or {}
		)
		self.block = self.block or zone.get("field")
		self.company = self.company or zone.get("owning_entity")

		concerns = self.concerns()
		self.contamination_detected = 1 if self._detected() else 0
		self.workflow_state = branch_state(
			"; ".join(concerns) or str(self.findings or "").strip(),
			bool(int(self.keep_as_draft or 0)),
		)

		if self.corrective_action_closed and str(self.corrective_action_closed) < str(self.test_date):
			frappe.throw(
				_("Corrective Action Closed {0} is before the sample on {1}.").format(
					self.corrective_action_closed, self.test_date
				)
			)
		if self.corrective_action_closed and not str(self.closure_note or "").strip():
			frappe.throw(
				_(
					"Closing a water finding needs a closure note saying what was done — treated, "
					"source switched, line flushed and re-tested."
				),
				title=_("Closure Note is required"),
			)

	def _detected(self) -> bool:
		return any(
			result_is_detection(self.get(fieldname)) is True
			for fieldname in ("coliform_result", "ecoli_result")
		) or bool(ecoli_over_action_level(self.ecoli_result))

	def concerns(self) -> list:
		"""Every reason this result is not a routine clean one, in sentences."""
		out = []
		for fieldname, label in (("coliform_result", "total coliform"), ("ecoli_result", "generic E. coli")):
			raw = str(self.get(fieldname) or "").strip()
			if not raw:
				continue
			verdict = result_is_detection(raw)
			if verdict is True:
				out.append(f"{label} was detected ({raw})")
			elif verdict is None:
				out.append(
					f"the {label} result {raw!r} could not be read as either a presence/absence "
					"answer or a count, so nobody can say whether this water is safe"
				)
		if ecoli_over_action_level(self.ecoli_result):
			out.append(
				f"generic E. coli is above the FSMA 112.44(b) criterion of "
				f"{ECOLI_ACTION_LEVEL_CFU} CFU/100 mL"
			)
		return out

	def on_update(self):
		"""Move the zone's AND the block's water test date forward."""
		if self.workflow_state not in COMPLETE_STATES:
			return
		writes = [advance_date("Irrigation Zone", self.source, ZONE_DATE_FIELD, self.test_date)]
		if self.block:
			writes.append(advance_date("Field", self.block, BLOCK_DATE_FIELD, self.test_date))
		self.flags.register_writes = writes


def open_corrective_action(row: dict) -> bool:
	"""Does this test still have a contamination finding nobody has closed?"""
	from erpnext_mcp.records import CORRECTIVE_ACTION_REQUIRED

	if str(row.get("workflow_state") or "") != CORRECTIVE_ACTION_REQUIRED:
		return False
	return not str(row.get("corrective_action_closed") or "").strip()
