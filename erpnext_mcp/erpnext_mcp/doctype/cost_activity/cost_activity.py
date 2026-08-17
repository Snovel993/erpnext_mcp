# SPDX-License-Identifier: MIT
"""Controller for Cost Activity — one thing the operation does that costs money.

WHAT `validate` REFUSES, AND WHY EACH REFUSAL IS HERE RATHER THAN IN THE TOOL.
Two activities with the same name in one company, because the whole register is
read by name in reports and two rows called "Dormant spray" make every per-acre
figure ambiguous. And a duplicated account row, because an account counted twice
doubles that slice of the pool silently — the pool total still looks plausible,
which is what makes it dangerous.

WHAT IT DELIBERATELY DOES NOT REFUSE is an activity naming neither a cost center
nor an account. That activity simply cannot have a LEDGER pool, and
`create_activity_cost_pool` says so in the sentence it returns rather than being
refused here. An operation whose chart is not yet arranged around its activities
still needs to be able to write down what its activities are.
"""

import frappe
from frappe import _
from frappe.model.document import Document

#: The drivers this app can work out for itself from what the site already
#: holds. Everything else is a measurement somebody took, and the allocation
#: engine requires it to be supplied rather than estimated.
DERIVABLE_DRIVERS = ("Acres", "Direct Assignment")


class CostActivity(Document):
	def autoname(self):
		abbr = frappe.db.get_value("Company", self.company, "abbr") or ""
		stem = str(self.activity_name or "").strip()
		self.name = f"{stem} - {abbr}" if abbr else stem

	def validate(self):
		self.activity_name = str(self.activity_name or "").strip()
		if not self.activity_name:
			frappe.throw(_("Activity is required — what this work is called on the ground."))

		self._refuse_a_duplicate_name()
		self._refuse_a_repeated_account()

	def _refuse_a_duplicate_name(self) -> None:
		# SELF IS EXCLUDED BY WHETHER THIS IS AN EDIT, NOT BY DOCNAME. `autoname`
		# derives the docname from the company and the activity name, so a second
		# row with the same two carries the SAME name — and a `name != self.name`
		# filter would quietly exclude the very twin it was written to find,
		# leaving the insert to fail later with Frappe's raw duplicate-key error,
		# which says nothing about why two activities of one name is a problem.
		# On an insert every match is a twin; only an edit has a self to skip.
		filters = {"company": self.company, "activity_name": self.activity_name}
		if not (self.flags.in_insert or self.is_new()):
			filters["name"] = ("!=", self.name or "")
		twin = frappe.db.get_all("Cost Activity", filters=filters, pluck="name", limit=1)
		if twin:
			frappe.throw(
				_(
					"Cost Activity {0} is already called {1} for {2}. Every ABC report groups by "
					"activity name, so two rows with one name would split one activity's cost "
					"across two lines in every per-acre figure this app produces — and the two "
					"halves would each look like a whole."
				).format(twin[0], self.activity_name, self.company),
				title=_("Duplicate activity"),
			)

	def _refuse_a_repeated_account(self) -> None:
		seen = set()
		for row in self.get("accounts") or []:
			# `row` is a Document on a site and a plain dict where a caller built
			# the child list by hand, so neither access style can be assumed.
			account = str((row.get("account") if isinstance(row, dict) else row.account) or "").strip()
			if not account:
				continue
			if account in seen:
				frappe.throw(
					_(
						"Account {0} is on this activity twice. A repeated account is totalled "
						"twice when the pool is computed, which does not look like an error — the "
						"pool comes out plausible and every block it reaches is overcharged by the "
						"same proportion."
					).format(account),
					title=_("Repeated account"),
				)
			seen.add(account)
