# SPDX-License-Identifier: MIT
"""Controller for IPM Recommendation Action — one option, and its rung.

Empty for the reason the other child tables here are: the sustainability score
is a property of the WHOLE set of actions — the mix of methods across the rows —
so it is computed on `IPMRecommendation.validate`, which can see all of them. A
row cannot score itself without knowing what it is being weighed against.
"""

from frappe.model.document import Document


class IPMRecommendationAction(Document):
	pass
