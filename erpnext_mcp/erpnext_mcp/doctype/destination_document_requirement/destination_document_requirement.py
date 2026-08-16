# SPDX-License-Identifier: MIT
"""Controller for Destination Document Requirement — shipping HERE needs THAT.

COUNTRY IS NORMALISED, AND THAT IS THE WHOLE CORRECTNESS ARGUMENT. The lookup
that builds a shipment's checklist matches on this column by exact value, so
'vietnam', 'Vietnam' and ' Viet Nam ' stored as three rows are three rules that
each apply to a different spelling of one country — and a shipment spelled the
fourth way gets none of them. A checklist that is silently short is the worst
failure this module has: it looks exactly like a shipment that needed nothing.
So the country is title-cased and squeezed here, on the way in, once.

A COUNTRY ON A LOCAL OR DOMESTIC RULE IS REFUSED rather than ignored. Somebody
who typed one meant something by it, and the tier lookup will never read it —
so the rule they thought they wrote would apply to every domestic shipment
instead of the one country they named.

DUPLICATES ARE REFUSED for the same reason a duplicate is always refused here:
two rules for one destination and one template mean one checklist row appearing
twice, and an operator turning the requirement off in the one they can find.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from ..trade_document_template.trade_document_template import (
	INTERNATIONAL,
	TIERS,
	normalise_tier,
)


def normalise_country(value) -> str:
	"""A country name in the one spelling this app stores, or "".

	NOT a validated list of ISO countries. A site's own Country doctype is the
	place with that list where it has one, and refusing a destination because a
	shipment is going somewhere this app's hard-coded table had not heard of
	would be an app with opinions about somebody else's trade. What is enforced
	is that one country has one spelling.
	"""
	raw = " ".join(str(value or "").split())
	if not raw:
		return ""
	# Title-case each word but leave anything already carrying an interior
	# capital alone — 'USA' and 'PNG' are not 'Usa' and 'Png'.
	words = []
	for word in raw.split(" "):
		words.append(word if any(char.isupper() for char in word[1:]) else word.title())
	return " ".join(words)[:140]


class DestinationDocumentRequirement(Document):
	def validate(self):
		self.destination_tier = normalise_tier(self.destination_tier)
		self.destination_country = normalise_country(self.destination_country)

		if self.destination_country and self.destination_tier != INTERNATIONAL:
			frappe.throw(
				_(
					"This rule is {0} but names the country {1}. The checklist lookup reads "
					"country only on {2} rules, so this would silently apply to EVERY {0} "
					"shipment rather than to the one country you named. Set the tier to {2}, "
					"or clear the country."
				).format(self.destination_tier, self.destination_country, INTERNATIONAL)
			)

		if not str(self.trade_document_template or "").strip():
			frappe.throw(_("A requirement has to name the document it requires."))

		if self.sequence in (None, ""):
			self.sequence = 50

		self._refuse_duplicate()

	def _refuse_duplicate(self) -> None:
		"""One destination, one template, one rule.

		Scoped by company too, because a blank company and a named one are
		genuinely different rules — the first is the site's default and the
		second is one entity's exception, and both may legitimately exist for
		the same destination and template.
		"""
		filters = {
			"destination_tier": self.destination_tier,
			"destination_country": self.destination_country or "",
			"trade_document_template": self.trade_document_template,
			"company": self.company or "",
			"name": ("!=", self.name),
		}
		try:
			clash = frappe.db.get_all(
				"Destination Document Requirement", filters=filters, fields=["name"], limit=1
			)
		except Exception:  # pragma: no cover - a site mid-migration
			return
		if clash:
			where = self.destination_country or self.destination_tier
			frappe.throw(
				_(
					"{0} already has a rule requiring {1} ({2}). Two rules for one destination "
					"and one document mean the same checklist row twice, and an operator "
					"switching the requirement off in whichever of them they happened to "
					"find. Edit that one instead."
				).format(where, self.trade_document_template, clash[0]["name"])
			)


def destination_label(tier: str, country: str = "") -> str:
	"""How a destination is named in a message somebody reads.

	One function because `get_destination_requirements`, the seeder and every
	refusal in this module all have to name a destination, and three spellings of
	'International — Japan' is how a search for one of them finds two.
	"""
	tier = str(tier or "").strip() or TIERS[0]
	country = normalise_country(country)
	return f"{tier} — {country}" if country else tier
