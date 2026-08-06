# SPDX-License-Identifier: MIT
"""Controller for Regulation Feed — a pointer at a regulation, as a record.

WHAT IT REFUSES, AND WHY EACH REFUSAL EARNED ITS PLACE:

  * A URL THAT IS NOT http(s). This field is handed to `requests.get` by a
    scheduled job, so `file:///etc/passwd` and `ftp://` are refused at the door
    rather than at three in the morning. The service checks the scheme again
    before it fetches — the same refusal at both ends, because a record written
    before this check existed would otherwise walk straight past it.
  * A FEED WITH NO URL, OR A URL WITH A SPACE IN IT. Both produce a feed that
    can never be checked, and a source that is silently never checked is worse
    than no source at all: the register says the regulation is being watched.
  * THE SAME RULE TWICE IN `affected_rules`. Deduplicated rather than refused —
    the set is what matters and a repeated row is a slip, not a decision — but
    deduplicated HERE so every reader downstream can count the rows and be
    right.

WHAT IT DOES NOT REFUSE, DELIBERATELY:

  * TWO FEEDS ON ONE URL. An operation whose ODA and OTCO obligations both turn
    on one page is entitled to two rows with two regimes and two descriptions,
    and the cost is one extra fetch a week.
  * A FEED WITH NO RULES ATTACHED. Most feeds start that way: somebody registers
    the source before anybody has written a rule from it, which is the right
    order to do it in.
  * A FEED WITH NO REGIME. `Internal` is the better answer and the tools say so,
    but a source nobody outside is coming to inspect is a real thing to watch.

THE FOUR STATE FIELDS ARE READ-ONLY ON THE FORM AND WRITTEN ONLY BY THE SERVICE.
`last_checked`, `last_content_hash`, `last_change_detected` and `change_log` are
the detector's own memory: a hash somebody typed is a change that will never be
reported, and a change log somebody edited is the one record here whose whole
value is that nobody edited it.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class RegulationFeed(Document):
	def validate(self):
		self._tidy()
		self._check_the_url()
		self._dedupe_rules()

	def _tidy(self) -> None:
		for fieldname in ("feed_name", "url", "last_content_hash"):
			self.set(fieldname, str(self.get(fieldname) or "").strip())
		if not self.status:
			self.status = "Active"
		if not self.check_frequency:
			self.check_frequency = "Weekly"
		# An error message on a feed that is not in Error is a sentence about
		# something that was fixed, sitting where a reader will take it for now.
		if self.status != "Error":
			self.error_message = None

	def _check_the_url(self) -> None:
		url = str(self.url or "")
		if not url:
			frappe.throw(
				_(
					"A Regulation Feed needs a URL. Without one this row says the regulation is "
					"being watched and nothing is watching it, which is worse than not having the "
					"row: the register is the thing somebody trusts."
				),
				title=_("No source"),
			)
		if not url.lower().startswith(("http://", "https://")):
			frappe.throw(
				_(
					"{0} is not an http(s) URL. This field is handed to an outbound HTTP request "
					"by a scheduled job, so a scheme that is not http or https is refused here "
					"rather than at three in the morning."
				).format(url),
				title=_("Not a fetchable URL"),
			)
		if any(character in url for character in " \t\n"):
			frappe.throw(
				_("The URL contains whitespace, so it is not a URL anything can fetch: {0!r}").format(url),
				title=_("Not a fetchable URL"),
			)

	def _dedupe_rules(self) -> None:
		seen = []
		rows = []
		for row in self.get("affected_rules") or []:
			rule = str((row.get("rule") if isinstance(row, dict) else getattr(row, "rule", "")) or "").strip()
			if not rule or rule in seen:
				continue
			seen.append(rule)
			rows.append(row)
		for index, row in enumerate(rows, start=1):
			# The ROWS ARE KEPT rather than rebuilt from their `rule` values, so a
			# save that changes nothing does not delete and recreate every child
			# row. Both spellings are handled because a row appended in this
			# request can still be a plain dict when a saved one is a Document.
			if isinstance(row, dict):
				row["idx"] = index
			else:
				row.idx = index
		self.set("affected_rules", rows)
