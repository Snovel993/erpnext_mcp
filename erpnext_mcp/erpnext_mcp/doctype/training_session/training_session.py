# SPDX-License-Identifier: MIT
"""Controller for Training Session — one afternoon, one curriculum, a room of people.

WHAT IT DERIVES, AND WHY EACH IS DERIVED HERE RATHER THAN AT THE CALL SITE. The
duration, the delivery method and the regimes all have an answer on the Training
Type, and a session created in the Desk has to get the same defaults as one
created through `create_training_session` — otherwise a curriculum corrected once
would be inherited by the tool's sessions and not by the Desk's, which is two
behaviours wearing one name.

THE GUARD IS AN EMPTY COLUMN, NOT `is_new()`. A crew leader who shortened a
session to forty minutes meant it, and a controller that copied the curriculum's
ninety back on every save would be overruling them on every edit — so a column
that holds something is never touched again. What "nobody has said" IS, is an
empty column, and that is true of a document built by a tool, by a patch and by
the Desk, none of which agree about what "new" means.

One consequence looks like a bug and is not: clearing a session's regimes
restores the curriculum's on the next save. That is deliberate. An untagged
session produces training records that appear in no audit packet, which is a
silent way to lose evidence, and inheriting a tag somebody can correct is better
than keeping an absence nobody will notice. A curriculum that itself carries no
regimes hands the session none, and `complete_training_session` refuses there —
which is the one place the absence has to be resolved by a person.

WHAT IT REFUSES:

  * AN END TIME BEFORE THE START. A session that finished before it began is a
    typo, and it is the typo that makes a computed duration negative.
  * THE SAME PERSON TWICE. Two rows for one attendee produce two training records
    of one afternoon, which is how a compliance matrix comes to disagree with the
    register about how many times somebody was trained.
  * AN ATTENDEE EMPLOYED BY ANOTHER COMPANY. The session's records are filed
    against the session's entity, and a record in the wrong register is evidence
    handed to an auditor asking about a different company.

WHAT IT DOES NOT REFUSE. A session against a Training Type somebody has since
deactivated — the training happened, and refusing the record of it is how an
operation ends up with the training and no evidence. A session with no attendees:
that is what Scheduled IS. An attendee with no badge and no signature — that row
simply produces no training record, and `complete_training_session` names it
rather than this refusing the save.

THE TRAINING RECORDS ARE NOT WRITTEN HERE. `complete_training_session` writes
them, in a tool, for the reason `inspection_session.py` gives about its own
compliance records: creating documents is an ACTION, and an action taken silently
by a save hook is one nobody can predict — including one that fires again on the
next save and files the afternoon twice.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp import compat, shifts, training, training_sessions


class TrainingSession(Document):
	def autoname(self):
		"""TRNS-YYYY-0001, where YYYY is the year the session RAN."""
		year = str(self.session_date or frappe.utils.today())[:4]
		self.name = shifts.next_in_series(training_sessions.DOCTYPE, "TRNS", year)

	def validate(self):
		if not self.status:
			self.status = training_sessions.STATUS_SCHEDULED
		if not self.session_date:
			self.session_date = frappe.utils.today()
		if not self.training_source:
			self.training_source = "Internal"

		self._inherit_from_the_curriculum()
		self._check_the_clock()
		self.conducted_by_name = (
			str(self.conducted_by_name or "").strip() or self._employee_name(self.conducted_by)
		)
		self._check_the_attendees()

	# ── the parts ───────────────────────────────────────────────────────────
	def _inherit_from_the_curriculum(self) -> None:
		"""Duration, delivery method and regimes off the Training Type, at creation only.

		`is_new()` is not the guard — an empty column is. A document built by a
		tool, by a patch and by the Desk do not agree about what "new" means, and
		the property this actually wants is "nobody has said", which is what an
		empty column IS.
		"""
		row = training_sessions.type_row(self.training_type)
		if not row:
			return
		if not int(self.duration_minutes or 0):
			self.duration_minutes = int(row.get("duration_minutes") or 0) or None
		if not str(self.delivery_method or "").strip():
			self.delivery_method = str(row.get("delivery_method") or "") or None
		if not self.get("regimes"):
			training.set_rows(self, "regimes", training.type_regimes(self.training_type))

	def _check_the_clock(self) -> None:
		start = training_sessions.clock(self.start_time)
		end = training_sessions.clock(self.end_time)
		self.start_time = start or None
		self.end_time = end or None
		if start and end and end < start:
			frappe.throw(
				_(
					"This session is recorded as starting at {0} and ending at {1}, which is "
					"before it began. Nothing was saved."
				).format(start, end),
				title=_("End before start"),
			)

	def _check_the_attendees(self) -> None:
		seen: dict = {}
		for row in self.get("attendees") or []:
			person = str(row.get("employee") or "").strip()
			if not person:
				frappe.throw(_("Every attendee row names an Employee. One of them does not."))
			if person in seen:
				frappe.throw(
					_(
						"{0} appears twice on this session's attendee list, at rows {1} and {2}. "
						"Two rows for one person produce two training records of one afternoon, "
						"which is how a compliance report comes to disagree with the register "
						"about how many times somebody was trained. Nothing was saved."
					).format(person, seen[person], row.idx),
					title=_("Attendee listed twice"),
				)
			seen[person] = row.idx
			row.employee_name = str(row.get("employee_name") or "").strip() or self._employee_name(person)
			self._check_the_attendees_company(person)

	def _check_the_attendees_company(self, person: str) -> None:
		if not (self.company and compat.doctype_exists("Employee")):
			return
		employer = frappe.db.get_value("Employee", person, "company")
		if employer and str(employer) != str(self.company):
			frappe.throw(
				_(
					"{0} is employed by {1} and this session belongs to {2}. Every record this "
					"session produces is filed against the session's entity, and a training "
					"record in another company's register is evidence handed to an auditor "
					"asking about a different company. Nothing was saved."
				).format(person, employer, self.company),
				title=_("Wrong company"),
			)

	def _employee_name(self, employee) -> str:
		if not (employee and compat.doctype_exists("Employee")):
			return ""
		return str(frappe.db.get_value("Employee", employee, "employee_name") or "")
