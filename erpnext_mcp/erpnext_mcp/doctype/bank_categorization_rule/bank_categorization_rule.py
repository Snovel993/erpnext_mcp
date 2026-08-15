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

#: Every match type the Select ships with. Kept here as well as in the JSON so
#: the evaluator and the form agree about the list — asserted equal by the tests.
#:
#: THE FIRST FOUR AND THE REST ARE TWO GENERATIONS AND BOTH ARE KEPT. v0.71.0
#: shipped an operator (`contains`) plus a separate field to apply it to, which
#: is the more general design and the one a site's existing rules are written in.
#: v0.73.0 adds the vocabulary a bank pipe already speaks — `merchant_contains`,
#: `description_regex` — where the field is part of the NAME. Nothing was
#: migrated, because a rule that says `contains CHEVRON on description` and one
#: that says `merchant_contains CHEVRON` are the same rule and rewriting sixty of
#: them would change what a site's audit trail says its rules were.
MATCH_TYPES = (
	"contains",
	"starts_with",
	"equals",
	"regex",
	"merchant_exact",
	"merchant_contains",
	"description_regex",
	"plaid_category_matches",
	"amount_range",
	"combined",
)

MATCH_FIELDS = ("description", "reference_number", "bank_party_name", "plaid_category")

DIRECTIONS = ("Any", "Deposit", "Withdrawal")

#: How each match type compares text, once the field has been chosen. A type
#: absent from here has no text criterion of its own.
TEXT_OPERATORS = {
	"contains": "contains",
	"starts_with": "starts_with",
	"equals": "equals",
	"regex": "regex",
	"merchant_exact": "equals",
	"merchant_contains": "contains",
	"description_regex": "regex",
	# A combined rule's text criterion is a plain substring. Anything sharper is
	# already expressible: the amount bounds and the direction are ANDed onto
	# EVERY match type, so `description_regex` with an amount ceiling is a
	# regex-and-amount rule without needing a second operator field here.
	"combined": "contains",
}

#: Match types that read a field the caller does not choose, because the type
#: names it. `merchant_*` reads the merchant and falls back to the description —
#: see `_merchant_text` for why that fallback is not a convenience.
IMPLIED_MATCH_FIELDS = {
	"merchant_exact": "bank_party_name",
	"merchant_contains": "bank_party_name",
	"description_regex": "description",
	"plaid_category_matches": "plaid_category",
}

#: The one match type with no text criterion at all.
TEXTLESS_MATCH_TYPES = ("amount_range",)


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
		for fieldname in ("rule_name", "category", "pattern", "plaid_category", "party_name"):
			value = self.get(fieldname)
			if isinstance(value, str):
				self.set(fieldname, value.strip())
		self.match_field = self.match_field or "description"
		self.match_type = self.match_type or "contains"
		self.direction = self.direction or "Any"

	def validate_pattern(self):
		"""Every match type has to carry the criterion its name promises.

		THE REFUSALS ARE ALL THE SAME SHAPE: a rule missing the thing it matches
		on does not fail at apply time, it matches NOTHING at apply time — and a
		rule that quietly matches nothing is indistinguishable from a rule whose
		transactions simply did not occur. Every one of these is cheap to refuse
		here and nearly invisible there.
		"""
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

		pattern = (self.pattern or "").strip()
		if self.match_type == "amount_range":
			if pattern:
				frappe.throw(
					_(
						"Match Type is amount_range, which has no text criterion, but a Pattern is set "
						"({0}). The pattern would be ignored, which is worse than being refused — use "
						"combined to match on text AND an amount."
					).format(pattern)
				)
			if self.amount_min in (None, "") and self.amount_max in (None, ""):
				frappe.throw(
					_(
						"Match Type is amount_range, so at least one of Amount Min and Amount Max is "
						"required. A range with no bounds matches every transaction on the account."
					)
				)
			return

		if self.match_type == "plaid_category_matches":
			if not (self.plaid_category or pattern):
				frappe.throw(
					_(
						"Match Type is plaid_category_matches, so Plaid Category is required — the "
						"feed's own category to match as a prefix, e.g. TRANSPORTATION."
					)
				)
			return

		if self.match_type == "combined":
			self.validate_combined(pattern)
			return

		if not pattern:
			frappe.throw(_("Pattern cannot be empty — a rule that matches everything is not a rule."))
		self.validate_regex(pattern)

	def validate_combined(self, pattern: str) -> None:
		"""A combined rule needs at least two of the things it is combining.

		One criterion is not a combination, it is one of the simpler match types
		written the long way round — and it would behave subtly differently,
		because `combined` compares text as a substring whatever `match_field`
		says. Refusing it here is what stops a rule reading as stricter than it is.
		"""
		criteria = []
		if pattern:
			criteria.append("Pattern")
		if self.plaid_category:
			criteria.append("Plaid Category")
		if self.amount_min not in (None, "") or self.amount_max not in (None, ""):
			criteria.append("an amount bound")
		if (self.direction or "Any") != "Any":
			criteria.append("Direction")
		if len(criteria) < 2:
			frappe.throw(
				_(
					"Match Type is combined, which ANDs its criteria, but only {0} is set. One "
					"criterion is not a combination — use contains, plaid_category_matches or "
					"amount_range, each of which says exactly what it does."
				).format(criteria[0] if criteria else "nothing at all")
			)
		if pattern:
			self.validate_regex(pattern)

	def validate_regex(self, pattern: str) -> None:
		"""A regular expression that will not compile is refused on SAVE.

		`re.compile` on a bad pattern raises `re.error`, and the place that would
		otherwise raise it is a loop over a month of transactions — one bad rule,
		and the operator gets a traceback instead of a categorisation run.
		"""
		if TEXT_OPERATORS.get(self.match_type) != "regex":
			return
		try:
			re.compile(pattern, re.IGNORECASE)
		except re.error as exc:
			frappe.throw(
				_(
					"Pattern {0} is not a valid regular expression: {1}. A rule that cannot compile "
					"would fail in the middle of a categorisation run rather than here."
				).format(frappe.utils.cstr(pattern), exc)
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
		for fieldname, doctype in (
			("account", "Account"),
			("cost_center", "Cost Center"),
			("bank_cost_center", "Cost Center"),
		):
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
		"""Whether this rule's text criterion matches `text`, case-insensitively.

		ONE FIELD'S WORTH OF THE DECISION, not the whole of it. A match type with
		no text criterion — `amount_range` — answers False here and is expected to;
		`matches_transaction` is the entire answer and is what a categorisation run
		asks. This stays public because two callers genuinely want the text half on
		its own: the overlap report, which asks whether one rule's pattern would
		also catch another's, and a person testing a pattern in the Desk.
		"""
		operator = TEXT_OPERATORS.get(self.match_type)
		if not operator:
			return False
		haystack = (text or "").strip().lower()
		needle = (self.pattern or "").strip().lower()
		if not haystack or not needle:
			return False
		if operator == "contains":
			return needle in haystack
		if operator == "starts_with":
			return haystack.startswith(needle)
		if operator == "equals":
			return haystack == needle
		if operator == "regex":
			try:
				return bool(self.compiled_pattern().search(text or ""))
			except re.error:
				# Refused on save, so this is a rule edited straight in the
				# database. It matches nothing rather than taking a run down.
				return False
		return False

	def compiled_pattern(self):
		"""This rule's regex, compiled ONCE and kept on the document.

		A categorisation run evaluates every rule against every transaction, so a
		book of sixty rules over a month of five hundred lines is thirty thousand
		evaluations. Recompiling inside that loop is the difference between a run
		that is instant and one an operator notices.
		"""
		cached = getattr(self, "_compiled_pattern", None)
		if cached is None:
			cached = re.compile(self.pattern or "", re.IGNORECASE)
			self._compiled_pattern = cached
		return cached

	def match_field_for(self) -> str:
		"""Which Bank Transaction field this rule's text criterion reads.

		The match types that name their own field win over `match_field`, because
		a rule that says `merchant_contains` and reads the reference number would
		be lying in its own name.
		"""
		return IMPLIED_MATCH_FIELDS.get(self.match_type) or (self.match_field or "description")

	def matches_transaction(self, row: dict) -> bool:
		"""The WHOLE decision for one transaction. The only thing a run should ask.

		THE ORDER IS DELIBERATE AND IT IS THE CHEAP CHECKS FIRST. Direction is a
		string comparison, the amount bounds are two float comparisons, and the
		text criterion is the one that can compile a regular expression — so a
		fuel rule capped at $2,000 rejects a wire transfer before it ever looks at
		the memo.

		DIRECTION AND THE AMOUNT BOUNDS APPLY TO EVERY MATCH TYPE, not just to
		`amount_range` and `combined`. That is what makes `amount_range` a match
		type at all rather than a modifier, and it is why a regex-plus-ceiling rule
		needs no special support: set the ceiling on a `description_regex` rule and
		both hold.

		`row` is a plain dict as the tools build it: `amount_signed`,
		`gross_amount`, `direction`, and whichever text fields the site has.
		"""
		direction = self.direction or "Any"
		if direction != "Any" and direction != row.get("direction"):
			return False

		gross = float(row.get("gross_amount") or 0)
		if self.amount_min not in (None, "") and gross < float(self.amount_min):
			return False
		if self.amount_max not in (None, "") and gross > float(self.amount_max):
			return False

		if self.match_type == "amount_range":
			return True

		if self.match_type == "plaid_category_matches":
			return self.matches_plaid_category(row.get("plaid_category"))

		if self.match_type == "combined":
			if self.plaid_category and not self.matches_plaid_category(row.get("plaid_category")):
				return False
			if not (self.pattern or "").strip():
				# Every other criterion has already been applied above. A combined
				# rule with no pattern is legal — the controller only requires two
				# criteria, and two of them can be an amount bound and a category.
				return True

		return self.matches_text(self._text_of(row))

	def matches_plaid_category(self, value) -> bool:
		"""Whether the feed's category is, or is under, the one this rule names.

		A PREFIX RATHER THAN AN EQUALITY, because these taxonomies are
		hierarchical and they GROW. A rule naming TRANSPORTATION catches
		TRANSPORTATION_GAS today and TRANSPORTATION_TOLLS the day the aggregator
		adds it; a rule that had to enumerate leaves would go quietly stale, which
		is the failure mode this whole doctype exists to avoid.
		"""
		wanted = str(self.plaid_category or self.pattern or "").strip().lower()
		actual = str(value or "").strip().lower()
		if not wanted or not actual:
			return False
		return actual == wanted or actual.startswith(wanted)

	def _text_of(self, row: dict) -> str:
		"""The text this rule reads off a transaction, with the merchant fallback.

		THE FALLBACK IS NOT A CONVENIENCE. A bank feed populates `bank_party_name`
		when the aggregator could identify a merchant and leaves it empty when it
		could not — and the transactions it cannot identify are disproportionately
		the small local suppliers a farm actually buys from. A `merchant_contains`
		rule that read only that column would match the national chains and miss
		the co-op, which is exactly backwards from what the rule was written for.
		"""
		fieldname = self.match_field_for()
		value = row.get(fieldname)
		if not value and fieldname == "bank_party_name":
			return str(row.get("description") or "")
		return str(value or "")
