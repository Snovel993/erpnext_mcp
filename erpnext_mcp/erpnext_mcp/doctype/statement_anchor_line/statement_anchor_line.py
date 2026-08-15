# SPDX-License-Identifier: MIT
"""Controller for Statement Anchor Line — the bank's own version of one line.

v0.73.0. Frappe imports a module for every DocType it loads, child tables
included, so this file exists whether or not it has anything to say. It does have
one thing to say: a Statement Anchor Line is NOT a Bank Transaction and must
never be reconciled against as though it were.

A Bank Transaction is what the feed delivered. A Statement Anchor Line is what
the paper statement printed. They are two records of the same event by two
different routes, and the ONLY reason to keep both is that the difference between
them is the thing nobody can otherwise see — a line on the statement with no
transaction behind it is a movement the feed dropped, and it is invisible in a
system that holds only the feed.
"""

from frappe.model.document import Document


class StatementAnchorLine(Document):
	pass
