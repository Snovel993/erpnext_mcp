# SPDX-License-Identifier: MIT
"""Controller for Employee Training Record — one afternoon in a shed, and the four audits it answers.

THE §112.161 FIELDS ARE ON THIS DOCTYPE FROM ITS FIRST VERSION, and that is the
only decision here worth defending at length.

FSMA 21 CFR 112.161 does not merely ask that a training happened. It asks that the
RECORD carry the farm's name, the date *and time* of the activity, the signature
or initials of the person who performed it, and — for worker-training records
specifically, under §112.161(b) — a supervisor's review, dated and signed within a
reasonable time after the record was made. An operation running USDA GAP alone
generally has none of the last three, which is why "missing supervisor sign-off on
the §112.161(b) records" is one of the most commonly cited FSMA findings against
farms whose actual practice is fine.

Adding those columns in v0.22 instead would mean every record written between now
and then has them empty, and there is no honest way to fill them later: a
signature backfilled in the week before an inspection is not evidence that
somebody signed at the time, it is evidence that somebody signed in the week
before the inspection. The columns are cheap now and impossible to retrofit
truthfully, so they are here.

────────────────────────────────────────────────────────────────────────────
STATUS IS COMPUTED AND THE ALERT ENGINE STILL DOES NOT TRUST IT
────────────────────────────────────────────────────────────────────────────

`status` is recomputed on every save from `expires_date` alone. That makes the
list view and the Desk form readable. It does NOT make the column authoritative,
and `alerts/rules.py` reads `expires_date` rather than `status` for the same
reason `Certification` does: a derived field is only correct on documents somebody
happened to save, so the expired training records would be exactly the ones still
reading Active. A column that is right when touched and wrong when ignored is
worse than no column, unless something else is doing the real work — and here the
hourly sweep is.

NO EXPIRY MEANS ACTIVE, FOREVER. A new-hire orientation and a PSA grower
certificate do not lapse. Assigning them a status that can become Expired would
put a renewal on the calendar that nobody can ever clear, which is how a
compliance calendar stops being read.

────────────────────────────────────────────────────────────────────────────
WHAT IT REFUSES, AND WHAT IT MERELY RECORDS
────────────────────────────────────────────────────────────────────────────

Refused: a training dated in the future (nobody has been taught yet); an expiry
before the completion (two dates transposed, every time); a supervisor review
dated before the training it reviews (§112.161(b) is a review of a record, and a
record that did not exist was not reviewed); a supervisor who is the trainee
(a review is a second pair of eyes or it is nothing); an unknown regime tag.

Merely recorded, with the gap stated: no signature, no supervisor review, no
topics, no certificate file. Every one of those is a §112.161 deficiency and none
of them is a reason to refuse the record — an operation that trained its crew and
recorded it imperfectly is in a better position than one that trained its crew and
recorded nothing, and a controller that refused would guarantee the second.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp import training


class EmployeeTrainingRecord(Document):
	def autoname(self):
		"""ETR-YYYY-MM-#####, so a docname reads as a month somebody can find.

		Same shape as Housing Inspection, and for the same reason: a compliance
		record is looked up in a list by a person who remembers roughly when, and
		a hash tells them nothing.
		"""
		month = str(self.completed_date or frappe.utils.today())[:7]
		prefix = f"ETR-{month}-"
		existing = frappe.db.get_all(
			training.DOCTYPE,
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
		self._require_the_two_facts()
		self._resolve_the_training_type()
		self._fill_from_the_employee()
		self._check_the_dates()
		self._normalise_the_tags()
		self._check_the_review()
		self.status = training.status_for(self.expires_date)
		self.activity_datetime = self._activity_datetime()

	# ── the parts ───────────────────────────────────────────────────────────
	def _require_the_two_facts(self) -> None:
		if not self.employee:
			frappe.throw(
				_(
					"Employee is required. §112.30(b) asks for the persons trained on the face "
					"of the record, and a training log nobody can reconcile to the payroll "
					"register is a log for somebody who may not have been on the farm that day."
				)
			)
		self.training_type = str(self.training_type or "").strip()
		if not self.training_type:
			frappe.throw(
				_(
					"Training Type is required — it is what the auditor reads first, and "
					"'training' is not an answer to 'trained in what'."
				)
			)
		if not self.completed_date:
			frappe.throw(_("Completed date is required. A training with no date is not a record."))

	def _resolve_the_training_type(self) -> None:
		"""Point `training_type` at a real Training Type, creating one if it is new.

		v0.19.2, AND IT RUNS IN `validate` RATHER THAN IN THE TOOL FOR ONE REASON:
		Frappe validates Links AFTER `validate` and BEFORE the row is written. A
		curriculum created here is therefore in place by the time the link is
		checked, and a curriculum created in `record_training` would cover that one
		caller and leave every other — the Desk form, a data import, a patch, the
		iOS app — throwing a link error at somebody who typed the true name of a
		course that genuinely happened.

		AUTO-CREATION IS RIGHT HERE AND WRONG FOR REGIMES, which this file also
		validates twenty lines down by REFUSING an unknown tag. The two are not
		inconsistent. Regimes are a closed legal vocabulary and 'OSHA' for
		'OR-OSHA' is a typo that files evidence where no packet looks. Curricula
		are open: regulators rename them, operations invent their own, and a name
		this site has not seen before is far more likely to be a real new course
		than a mistake. The failure modes point opposite ways, so the answers do.

		Matching is case- and whitespace-insensitive, so 'wps handler training'
		does not become a second master beside 'WPS Handler Training' and split one
		curriculum's history in two.
		"""
		if not str(self.training_type or "").strip():
			return
		try:
			outcome = training.ensure_type(self.training_type)
		except Exception:  # pragma: no cover - a site mid-migration
			# The column is still Data on a site that has not migrated yet. Leaving
			# the text as typed keeps the record filable, which matters more than
			# the link: a training that happened and was recorded against a name is
			# better evidence than a refusal.
			return
		if outcome.get("training_type"):
			self.training_type = outcome["training_type"]

	def _fill_from_the_employee(self) -> None:
		"""Name, company and farm name, denormalised onto the record itself.

		§112.161(a)(1)(i) wants the farm's name ON each record. A lookup through
		the Company link at report time answers with today's name; an inspector
		paging through last season's logs is entitled to the name that was on the
		gate then. So it is snapshotted, once, and never rewritten on later saves.
		"""
		row = frappe.db.get_value("Employee", self.employee, ["employee_name", "company"], as_dict=True) or {}
		if not str(self.employee_name or "").strip():
			self.employee_name = row.get("employee_name") or self.employee
		if not self.company:
			self.company = row.get("company")
		if not str(self.farm_name_snapshot or "").strip():
			self.farm_name_snapshot = self.company or None

	def _check_the_dates(self) -> None:
		today = frappe.utils.today()
		if str(self.completed_date) > today:
			frappe.throw(
				_(
					"Completed {0} is in the future — today is {1}. Nobody has been taught yet, "
					"and §112.161(a)(2) requires a record to be created at the time the activity "
					"is performed."
				).format(self.completed_date, today),
				title=_("Trained in the Future"),
			)
		if self.expires_date and str(self.expires_date) < str(self.completed_date):
			frappe.throw(
				_(
					"This training expires on {0} and was completed on {1} — it would have "
					"expired before it was delivered. Two dates transposed, almost certainly."
				).format(self.expires_date, self.completed_date),
				title=_("Expires Before Completion"),
			)

	def _normalise_the_tags(self) -> None:
		"""Canonical regime spellings, or a refusal naming what was not understood.

		Refused rather than dropped: a record tagged 'OSHA' where the vocabulary
		says 'OR-OSHA' is a record that will be missing from the OR-OSHA packet and
		present in none, and nobody finds that out until an inspector does.
		"""
		raw = self.regimes
		if not str(raw or "").strip():
			self.regimes = ""
		else:
			try:
				self.regimes = training.join(training.require(raw))
			except ValueError as exc:
				frappe.throw(str(exc), title=_("Unknown Regime"))
		self.content_topics_covered = training.topics_text(self.content_topics_covered)

	def _check_the_review(self) -> None:
		if self.supervisor_reviewed_by and str(self.supervisor_reviewed_by) == str(self.employee):
			frappe.throw(
				_(
					"{0} cannot review their own training record. §112.161(b) asks for a "
					"supervisor or responsible party, which is a second pair of eyes or it is "
					"nothing."
				).format(self.supervisor_reviewed_by),
				title=_("Self-Review"),
			)
		if self.supervisor_reviewed_on and self.completed_date:
			if str(self.supervisor_reviewed_on)[:10] < str(self.completed_date):
				frappe.throw(
					_(
						"The supervisor review is dated {0} and the training was completed on "
						"{1}. §112.161(b) is a review OF A RECORD, and a record that did not "
						"exist yet was not reviewed."
					).format(self.supervisor_reviewed_on, self.completed_date),
					title=_("Reviewed Before It Happened"),
				)
		if self.supervisor_reviewed_on and not self.supervisor_reviewed_by:
			frappe.throw(
				_(
					"A supervisor review date with nobody attached is what an auditor is trained "
					"to disbelieve. Set Supervisor Reviewed By as well."
				),
				title=_("Review Has No Reviewer"),
			)

	def _activity_datetime(self) -> str:
		"""Date and time as the single stamp §112.161(a)(1)(v) asks for.

		Midnight where no time was recorded, which is the honest reading: the day
		is known and the hour is not. Inventing a plausible hour would be worse
		than admitting the gap, because the gap is what `fsma_161_gaps` reports.
		"""
		date = str(self.completed_date or "")[:10]
		if not date:
			return ""
		time = str(self.completed_time or "").strip()
		if not time:
			return f"{date} 00:00:00"
		parts = time.split(":")
		while len(parts) < 3:
			parts.append("00")
		return f"{date} {':'.join(part.zfill(2)[:2] for part in parts[:3])}"
