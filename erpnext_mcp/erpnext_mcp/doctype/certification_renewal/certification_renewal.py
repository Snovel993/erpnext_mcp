# SPDX-License-Identifier: MIT
"""Controller for Certification Renewal — a child table, and empty on purpose.

Frappe imports one module per DocType, child tables included; a folder with a
JSON and no module breaks `bench migrate`. See `asset_depreciation_posting.py`.

THE ONE RULE THAT MATTERS ABOUT A RENEWAL — that it must not move the expiration
BACKWARDS, which would be a correction recorded as a renewal and would put a term
in the history that never existed — is enforced on the parent in
`certification.py`. It could arguably live here, since both dates are on the row.
It does not, because the OTHER half of the same judgement cannot: whether a
renewal was recorded after the previous expiration, and therefore how long the
certificate was allowed to lapse, is read across the row and the parent's current
state. Splitting one judgement across two controllers is how the two halves
eventually disagree.
"""

from frappe.model.document import Document


class CertificationRenewal(Document):
	pass
