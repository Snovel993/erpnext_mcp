# SPDX-License-Identifier: MIT
"""Controller for Inspection Session — one worker's afternoon at one place.

THE VERSION IS PINNED HERE, ON INSERT, AND NEVER AGAIN. `template_version` is
copied off the template row the moment the session is created and is read-only
afterwards. That single line is what makes a session from April readable in
November: templates are versioned by copy, so the row this session links is
itself the version, and the number merely states it where a printed packet can
carry it without a second lookup.

There is deliberately no window in which a running session's definition changes
underneath it. `update_inspection_template` writes a NEW row; the row this
session points at is never edited, so a session started against v1 while
somebody is authoring v2 sees v1 all the way to submission — including the
sections, including their contracts.

WHAT IT REFUSES:

  * A SESSION AGAINST A TEMPLATE THAT IS NOT LIVE. Deactivated or superseded,
    the answer is the same: new work does not start from it. Sessions already
    worked from it stay fully readable, which is the whole point of deactivating
    rather than deleting.
  * A LOCATION IN THE WRONG REGISTER. Where the template's `applies_to_asset_type`
    names a DocType this site has, `location_doctype` must be it. Where it names
    a label — Sprayer, Cabin, General — nothing is enforced, because a refusal
    this app keeps no register behind is a refusal it cannot justify.
  * A LOCATION THAT IS NOT ON THE REGISTER IT CLAIMS. A session at a cabin
    nobody has heard of is not evidence about anything.

WHAT IT DOES NOT REFUSE. A session with no worker named — a foreman starting one
on somebody's behalf before the shift is assigned is real. A session with no
evidence yet: that is what Draft IS.

THE COMPLIANCE RECORDS ARE NOT WRITTEN HERE. `submit_inspection_session` writes
them, in a tool, for the reason `detector_test.py` gives about its replacement
task: creating documents is an ACTION, and an action taken silently by a save
hook is one nobody can predict — including one that fires again on the next save.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp import compat, sessions, shifts


class InspectionSession(Document):
	def autoname(self):
		"""INSPS-YYYY-0001, where YYYY is the year the visit STARTED."""
		year = str(self.started_at or frappe.utils.now())[:4]
		self.name = shifts.next_in_series(sessions.SESSION_DOCTYPE, "INSPS", year)

	def validate(self):
		if not self.state:
			self.state = sessions.STATE_DRAFT
		if not self.started_at:
			self.started_at = frappe.utils.now()

		row = self._template_row()
		# "At creation" is read off the PIN rather than off `is_new()`. The
		# version is written once and never again, so an unset one is the only
		# state in which this session has not yet been fixed to a template — and
		# that is true of a document built by a tool, by the rule engine, or by
		# somebody in the Desk, none of which agree about what "new" means.
		if not int(self.template_version or 0):
			self._pin_the_version(row)
			self._refuse_a_template_that_is_not_live(row)
		self._check_the_location(row)

		if self.state in (sessions.STATE_SUBMITTED, sessions.STATE_REVIEWED) and not self.submitted_at:
			self.submitted_at = frappe.utils.now()

		self.worker_name = str(self.worker_name or "").strip() or self._employee_name(self.worker)
		self.foreman_name = str(self.foreman_name or "").strip() or self._employee_name(self.foreman)
		if not self.company and self.location_doctype == "Housing Unit" and self.location:
			self.company = frappe.db.get_value("Housing Unit", self.location, "owning_entity")

	# ── the parts ───────────────────────────────────────────────────────────
	def _template_row(self) -> dict:
		if not self.template:
			frappe.throw(_("Template is required — a session is an execution of one."))
		row = sessions.template_row(self.template)
		if not row:
			frappe.throw(
				_(
					"No Inspection Template called {0} on this site. list_inspection_templates has them."
				).format(self.template)
			)
		return row

	def _pin_the_version(self, row: dict) -> None:
		self.template_version = int(row.get("version") or 1)

	def _refuse_a_template_that_is_not_live(self, row: dict) -> None:
		if not compat.checked(row.get("active")):
			frappe.throw(
				_(
					"Inspection Template {0} is not active, so no new session starts from it. "
					"Every session already worked from it stays readable and every compliance "
					"record it produced stays in the register — that is what deactivating is FOR."
				).format(self.template),
				title=_("Template is not active"),
			)
		if str(row.get("superseded_by") or "").strip():
			frappe.throw(
				_(
					"Inspection Template {0} was superseded by {1}. Start the session from that "
					"one — or from the template's name, which always resolves to the live version."
				).format(self.template, row["superseded_by"]),
				title=_("Template was superseded"),
			)

	def _check_the_location(self, row: dict) -> None:
		self.location = str(self.location or "").strip()
		if not self.location:
			frappe.throw(_("Location is required. A session that does not say where it was is not evidence."))
		asset_type = str(row.get("applies_to_asset_type") or "").strip()
		if asset_type in sessions.MATCHABLE_ASSET_TYPES:
			if not self.location_doctype:
				self.location_doctype = asset_type
			elif str(self.location_doctype) != asset_type:
				frappe.throw(
					_(
						"Template {0} applies to {1}, and this session names a {2}. A cabin "
						"template worked against a block produces compliance records about the "
						"wrong thing."
					).format(self.template, asset_type, self.location_doctype),
					title=_("Wrong register"),
				)
		if self.location_doctype and frappe.db.exists("DocType", str(self.location_doctype)):
			if not frappe.db.exists(str(self.location_doctype), self.location):
				frappe.throw(
					_("No {0} called {1} on this site. Nothing was created.").format(
						self.location_doctype, self.location
					)
				)

	def _employee_name(self, employee) -> str:
		if not (employee and compat.doctype_exists("Employee")):
			return ""
		return str(frappe.db.get_value("Employee", employee, "employee_name") or "")
