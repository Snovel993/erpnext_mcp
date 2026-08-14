# SPDX-License-Identifier: MIT
"""Controller for Bank Categorization Rule — the statement line's own dictionary.

v0.71.0, Sprint 6. A bank feed writes a memo line and nothing else: `CHEVRON
0093746 PASCO WA`. Somebody has to say that this is fuel, that fuel books to
5200, and that the vendor is the co-op. Doing that by hand once per line is a
day a week in season; doing it in code is a release every time a farm changes
fuel suppliers. So it is a RECORD — the same argument the Compliance Rule
doctype makes about compliance, applied to the chart of accounts.

EVERYTHING BELOW EXISTS BECAUSE A RULE IS EVALUATED THOUSANDS OF TIMES AND
EDITED ONCE. A pattern that cannot compile, a range whose floor is above its
ceiling, a rule pointed at another company's account — each of those is a
mistake that costs nothing to refuse here and is nearly invisible at apply time,
where it shows up as transactions that quietly did not match.

THE REGEX IS COMPILED ON SAVE, NOT ON APPLY. `re.compile` on a bad pattern
raises `re.error`, and the place that would raise it otherwise is a loop over a
month of transactions — one bad rule, and the operator gets a traceback instead
of a categorisation run. Compiling here turns that into a form validation with
the offending pattern quoted.

THE UNIQUENESS CHECK IS PER COMPANY, AND THAT IS WHY THE DOCNAME IS A SERIES.
Two farms on one site both have a rule called "Fuel — Chevron", and naming the
document after the rule would make the second one a duplicate of the first.
Naming from a series and validating (company, rule_name) instead lets each
company keep its own book of rules with the names its bookkeeper already uses.

WHAT THIS DOCTYPE DELIBERATELY DOES NOT DO. It does not post, it does not
allocate, and it does not touch the ledger. A rule fills in three fields on a
Bank Transaction — what this is, where it books, who it was — and the decision
about what to *do* with a categorised transaction stays with a person and with
the Journal Entry tools, which have their own switches.
"""

import re

import frappe
from frappe import _
from frappe.model.document import Document

#: Every match type the Select ships with, and what each means against a field
#: that has been lower-cased first. Kept here as well as in the JSON so the
#: evaluator and the form agree about the list — asserted equal by the tests.
MATCH_TYPES = ("contains", "starts_with", "equals", "regex")

MATCH_FIELDS = ("description", "reference_number", "bank_party_name")

DIRECTIONS = ("Any", "Deposit", "Withdrawal")


class BankCategorizationRule(Document):
	def validate(self):
		self.normalise()
		self.validate_pattern()
		self.validate_amount_range()
		self.validate_priority()
		self.validate_account_company()
		self.validate_unique_name()

	def normalise(self):
		"""Trim the strings a person pasted, and default what the form left empty.

		The trim matters more than it looks: a pattern of `"CHEVRON "` with a
		trailing space matches nothing under `equals` and matches oddly under
		`contains`, and the space is invisible in the form that created it.
		"""
		for fieldname in ("rule_name", "category", "pattern"):
			value = self.get(fieldname)
			if isinstance(value, str):
				self.set(fieldname, value.strip())
		self.match_field = self.match_field or "description"
		self.match_type = self.match_type or "contains"
		self.direction = self.direction or "Any"

	def validate_pattern(self):
		"""A pattern that is empty, or a regex that will not compile, is refused."""
		if not (self.pattern or "").strip():
			frappe.throw(_("Pattern cannot be empty — a rule that matches everything is not a rule."))
		if self.match_type not in MATCH_TYPES:
			frappe.throw(
				_("Match Type must be one of: {0} — got {1}.").format(", ".join(MATCH_TYPES), self.match_type)
			)
		if self.match_field not in MATCH_FIELDS:
			frappe.throw(
				_("Match Field must be one of: {0} — got {1}.").format(
					", ".join(MATCH_FIELDS), self.match_field
				)
			)
		if self.match_type != "regex":
			return
		try:
			re.compile(self.pattern, re.IGNORECASE)
		except re.error as exc:
			frappe.throw(
				_(
					"Pattern {0} is not a valid regular expression: {1}. A rule that cannot compile "
					"would fail in the middle of a categorisation run rather than here."
				).format(frappe.utils.cstr(self.pattern), exc)
			)

	def validate_amount_range(self):
		"""A floor above its ceiling matches nothing, silently, forever."""
		low = self.amount_min
		high = self.amount_max
		if low in (None, "") or high in (None, ""):
			return
		if float(low) > float(high):
			frappe.throw(
				_(
					"Amount Min ({0}) is above Amount Max ({1}), so this rule can never match anything."
				).format(low, high)
			)

	def validate_priority(self):
		"""Priority is an order, and a negative one still orders — but zero is the floor.

		Frappe stores an unset Int as 0, which would put every rule somebody
		forgot to prioritise ahead of every rule somebody thought about. The
		default in the JSON is 100 for that reason; this only refuses the
		negative values that would defeat it.
		"""
		if self.priority is not None and int(self.priority or 0) < 0:
			frappe.throw(_("Priority cannot be negative. Lower runs first; 0 is the front of the queue."))

	def validate_account_company(self):
		"""A rule cannot book one company's transactions into another's ledger."""
		for fieldname, doctype in (("account", "Account"), ("cost_center", "Cost Center")):
			target = self.get(fieldname)
			if not target:
				continue
			owner = frappe.db.get_value(doctype, target, "company")
			if owner and self.company and owner != self.company:
				frappe.throw(
					_("{0} {1} belongs to {2}, but this rule is for {3}.").format(
						doctype, target, owner, self.company
					)
				)
		if self.account and frappe.db.get_value("Account", self.account, "is_group"):
			frappe.throw(
				_("{0} is a group account, and ERPNext posts only to leaf accounts.").format(self.account)
			)

	def validate_unique_name(self):
		"""One rule name per company. See the module docstring for why not per site."""
		if not self.rule_name or not self.company:
			return
		clash = frappe.db.get_value(
			self.doctype,
			{"rule_name": self.rule_name, "company": self.company, "name": ("!=", self.name or "")},
			"name",
		)
		if clash:
			frappe.throw(
				_(
					"{0} already has a rule called {1} ({2}). Two rules with one name are "
					"indistinguishable in the report that says which rule categorised a "
					"transaction."
				).format(self.company, self.rule_name, clash)
			)

	# ── evaluation ───────────────────────────────────────────────────────────
	#
	# Lives on the controller rather than in the tool module so the Desk, a
	# scheduled job and the MCP tool all decide "does this rule match" the same
	# way. A second implementation is a second answer.

	def matches_text(self, text: str) -> bool:
		"""Whether this rule's pattern matches `text`, case-insensitively."""
		haystack = (text or "").strip().lower()
		needle = (self.pattern or "").strip().lower()
		if not haystack or not needle:
			return False
		if self.match_type == "contains":
			return needle in haystack
		if self.match_type == "starts_with":
			return haystack.startswith(needle)
		if self.match_type == "equals":
			return haystack == needle
		if self.match_type == "regex":
			try:
				return bool(re.search(self.pattern, text or "", re.IGNORECASE))
			except re.error:
				# Refused on save, so this is a rule edited straight in the
				# database. It matches nothing rather than taking a run down.
				return False
		return False
