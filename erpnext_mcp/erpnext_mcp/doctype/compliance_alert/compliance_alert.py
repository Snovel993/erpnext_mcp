# SPDX-License-Identifier: MIT
"""Controller for Compliance Alert — the one doctype here nobody types into.

EVERY OTHER DOCTYPE IN THIS APP RECORDS A DECISION SOMEBODY MADE. This one
records a conclusion the software reached from records somebody else made, which
changes what the controller is for: there is no operator to protect from a typo,
so the invariants worth enforcing are the ones that keep the nightly sweep
honest.

THE KEY IS THE IDEMPOTENCE. `alert_key` is derived from the rule and the source
record — never from a date, never from a message — so tonight's sweep finds the
same key it wrote last night and refreshes that row rather than adding a second
one. If the key carried the due date, a certificate moving from 60 days out to 59
would spawn a new alert every single night and quietly discard the snooze
somebody set on the old one. That failure is silent and it accumulates, which is
why the key is computed in exactly one place (`alerts.base.alert_key`) and this
controller refuses a document that arrives without one.

A DISMISSAL BY A PERSON NEEDS A REASON; AN AUTO-DISMISSAL MUST NOT HAVE ONE.
Those are two different events that would otherwise be indistinguishable six
months later. "The sweep noticed the water test was done" and "a human decided
this did not matter" are answers to different auditor questions, and the second
one is the one that needs defending.

`first_seen` IS NEVER MOVED FORWARD. An alert that has been open four months is
evidence of four months of not doing something. A refresh that reset it would
turn a chronic problem into a new one every morning, which is precisely the
reporting a dashboard exists to prevent.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class ComplianceAlert(Document):
	def validate(self):
		if not str(self.alert_key or "").strip():
			frappe.throw(
				_(
					"A Compliance Alert needs an alert_key. It is derived from the rule and the "
					"source record by erpnext_mcp.alerts, and it is what stops the nightly sweep "
					"writing a second copy of an alert it already raised."
				),
				title=_("No Alert Key"),
			)
		if not str(self.alert_message or "").strip():
			frappe.throw(
				_("An alert with no message is a row somebody has to open the source record to understand.")
			)

		if not self.first_seen:
			self.first_seen = frappe.utils.today()

		dismissed = frappe.utils.cint(self.dismissed)
		auto = frappe.utils.cint(self.auto_dismissed)
		reason = str(self.dismissed_reason or "").strip()

		if auto and not dismissed:
			frappe.throw(
				_("auto_dismissed is set and dismissed is not. An auto-dismissal is a dismissal."),
				title=_("Half-Dismissed"),
			)
		if auto and reason:
			frappe.throw(
				_(
					"An auto-dismissed alert must not carry a human's reason. The sweep dismissed "
					"it because the source condition resolved, and recording a person's judgement "
					"beside that would make the two indistinguishable later."
				),
				title=_("Reason On An Auto-Dismissal"),
			)
		if dismissed and not auto and not reason:
			frappe.throw(
				_(
					"Dismissing an alert by hand needs a reason. It is the only part of this "
					"record nobody can reconstruct — the alert itself the sweep can rebuild, but "
					"why somebody decided it did not need doing exists nowhere else."
				),
				title=_("Dismissed Without A Reason"),
			)
		if dismissed and not self.dismissed_on:
			self.dismissed_on = frappe.utils.today()

	def visible_on(self, today: str) -> bool:
		"""Whether this alert belongs on the calendar as of `today`.

		Dismissed is gone. Snoozed is gone until the snooze runs out — and then it
		is back, without the sweep having to do anything, because a snooze is a
		date rather than a flag somebody has to clear.
		"""
		if frappe.utils.cint(self.dismissed):
			return False
		snoozed = str(self.snoozed_until or "")
		return not (snoozed and snoozed >= today)
