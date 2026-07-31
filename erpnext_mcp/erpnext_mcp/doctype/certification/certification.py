# SPDX-License-Identifier: MIT
"""Controller for Certification — a certificate, and the date it stops defending you.

THE EXPIRATION DATE IS THE POINT OF THE RECORD. Everything else here is context
for it. An operation with a lapsed GlobalGAP certificate is not an operation with
a paperwork problem, it is an operation whose fruit cannot be sold to the buyer
who required it; an applicator whose licence lapsed cannot lawfully spray. So the
alert engine reads `expiration_date` and NOT `status`, and this controller does
not quietly flip `status` to Expired when a date passes.

That last decision is deliberate and worth defending, because the obvious
alternative is worse. A controller that rewrote `status` on validate would only
do it on documents somebody happened to save — so the expired certificates would
be exactly the ones still reading Active, and a list filtered on status would
show the lapsed ones as current. A derived field that is only correct when
touched is worse than no derived field. `status` records what somebody decided
(suspended, revoked, superseded); the dates record what is true; the alert engine
compares the dates to today and is right every night without anybody saving
anything.

WHAT IT REFUSES. An expiration before the issue date, which is a transposition
every time. A negative renewal window. Both are refused rather than corrected:
guessing which of two dates the typist meant is how a certificate ends up
recorded as valid for a year it was not.

THE RENEWAL WINDOW IS A LEAD TIME, NOT A PREFERENCE. It defaults to 90 days
because that is roughly what an Oregon farm labor contractor licence renewal
actually takes — bond, background check, agency queue. Setting it to 7 does not
make the agency faster; it makes the alert arrive too late to act on.
"""

import frappe
from frappe import _
from frappe.model.document import Document

#: Below this, a renewal window is not a lead time — it is a reminder, and the
#: alert it drives arrives after the last day anything could have been done about
#: it. Warned about rather than refused: a first-aid card really can be renewed in
#: an afternoon.
SHORT_WINDOW_DAYS = 14


class Certification(Document):
	def validate(self):
		self.cert_name = str(self.cert_name or "").strip()
		if not self.cert_name:
			frappe.throw(
				_("Certificate Name is required — it is the docname, and it is what an audit asks for.")
			)

		if self.issued_date and self.expiration_date and self.expiration_date < self.issued_date:
			frappe.throw(
				_(
					"This certificate expires on {0} and was issued on {1} — it would have "
					"expired before it was issued. Two dates transposed, almost certainly."
				).format(self.expiration_date, self.issued_date),
				title=_("Expires Before Issue"),
			)

		window = int(self.renewal_window_days or 0)
		if window < 0:
			frappe.throw(
				_("Renewal Window cannot be negative — it is how many days BEFORE expiry the renewal has to start."),
				title=_("Negative Renewal Window"),
			)

		for row in self.get("renewals") or []:
			if row.get("previous_expiration") and row.get("new_expiration"):
				if str(row.new_expiration) < str(row.previous_expiration):
					frappe.throw(
						_(
							"A renewal on {0} moves the expiration BACKWARDS, from {1} to {2}. "
							"That is a correction, not a renewal, and recording it as one would "
							"put a lapse in the history that never happened."
						).format(row.get("renewed_on"), row.previous_expiration, row.new_expiration),
						title=_("Renewal Moves Backwards"),
					)
