# SPDX-License-Identifier: MIT
"""Controller for Merchant Alias — the spelling a receipt printer uses, taught once.

v0.75.0. `normalize_merchant` can get from "WILBUR ELLIS CO" to "Wilbur-Ellis
Company LLC" because the letters are the same letters. It can never get from
"SIATAPING" to "Sawyer's Ace Hardware", because they are not — that string is a
point-of-sale terminal's abbreviation of a franchise name and there is no
algorithm that recovers it. Somebody has to say so once. This is where what
they said is kept.

THE DOCNAME IS THE NORMALISED ALIAS, AND THAT IS THE WHOLE DESIGN. "one alias
resolves to exactly one Supplier" is the invariant the lookup depends on: a
cascade that could get two answers from its highest-confidence step is a
cascade with no highest-confidence step. Making the key the primary key means
the database holds that invariant rather than a validation somebody can forget
to run, and it means 'Valley Co-op #14' and 'VALLEY CO-OP 14' are one row
instead of two rows that will eventually disagree.

THE NORMALISATION IS `receipts._normalize_merchant_name`, NOT A SECOND COPY OF
IT. The key a row is stored under and the key a receipt is looked up by have to
be produced by the same function or the table quietly stops matching — which is
a failure with no error in it, just a suggestion that never appears again.

RE-POINTING AN ALIAS IS ALLOWED, AND IT IS NOT A MISTAKE. A bookkeeper who
links "CASCADE AG" to a different Supplier than the one it resolved to last
month has corrected something, and the correction is the more recent human
decision — so the tool layer moves the link and leaves the count alone. What is
NOT allowed is two rows for one key, which is what the docname prevents.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp.tools import receipts

DOCTYPE = "Merchant Alias"


class MerchantAlias(Document):
	def autoname(self):
		"""The normalised alias IS the docname. See the module docstring."""
		self.alias_key = receipts.alias_key(self.get("alias"))
		if not self.alias_key:
			frappe.throw(
				_(
					"{0} normalises to an empty key — it is punctuation and legal-form words "
					"and nothing else, so there is no spelling here to teach. Nothing was saved."
				).format(self.get("alias"))
			)
		self.name = self.alias_key

	def validate(self):
		self._keep_the_key_in_step()
		self._check_source()
		self._floor_the_count()

	def _keep_the_key_in_step(self) -> None:
		"""A row whose key no longer matches its alias is a row nothing finds.

		Recomputed on every save rather than only at insert, because `alias` is
		editable in the Desk and an edit that left the key behind would leave a
		mapping in the table that no receipt can ever reach — invisible, and
		indistinguishable from the mapping never having been taught.
		"""
		self.alias_key = receipts.alias_key(self.get("alias"))
		if not self.alias_key:
			frappe.throw(
				_("{0} normalises to an empty key, so nothing could look it up. Nothing was saved.").format(
					self.get("alias")
				)
			)

	def _check_source(self) -> None:
		"""The Select, enforced here as well as at the tool layer.

		Frappe does not enforce Select options on a programmatic save, and a
		source of "manual" in the wrong case would sit in the column and then
		never match the filter that decides whether a replay of this mapping is
		treated as a person's own decision or as a guess.
		"""
		source = self.get("source") or receipts.ALIAS_MANUAL
		if source not in receipts.ALIAS_SOURCES:
			frappe.throw(
				_("{0} is not an alias source. It is one of: {1}. Nothing was saved.").format(
					source, ", ".join(receipts.ALIAS_SOURCES)
				)
			)
		self.source = source

	def _floor_the_count(self) -> None:
		"""`match_count` counts receipts and cannot be negative.

		Clamped rather than refused: the figure is maintained by the resolver,
		nobody has business setting it by hand, and taking down a save over it
		would be a refusal that teaches nothing.
		"""
		self.match_count = max(0, int(self.get("match_count") or 0))
