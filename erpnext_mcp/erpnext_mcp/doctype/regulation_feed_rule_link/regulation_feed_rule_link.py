# SPDX-License-Identifier: MIT
"""Controller for Regulation Feed Rule Link — a child table, and empty on purpose.

Frappe imports one module per DocType, child tables included, and a folder with a
JSON and no module breaks `bench migrate` rather than degrading. That is why this
file exists at all; see `compliance_regime_link.py` for the same note and
`test_packaging.py` for the release that learned it the hard way.

THERE IS NOTHING TRUE OF ONE OF THESE ROWS ON ITS OWN. A row says "this rule was
written from that source", and every question worth asking about the claim — is
the rule still live, which version of it, what the source says now — is a
question about the two documents it joins, neither of which the row can see. So
the reading happens in `erpnext_mcp/services/regulation_feed.py`, which is the
one place that turns a set of these rows into the sentence a change log entry
carries.
"""

from frappe.model.document import Document


class RegulationFeedRuleLink(Document):
	pass
