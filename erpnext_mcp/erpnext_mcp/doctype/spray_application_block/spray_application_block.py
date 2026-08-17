# SPDX-License-Identifier: MIT
"""Controller for Spray Application Block — one block one pass reached.

The rules that matter — one row per block, non-negative acres, a block
completion inside the application's own window — are all comparisons against the
other rows or against the parent, so they live on `SprayApplication.validate`.
See the note in `spray_tank_mix_product.py`, which is the same argument.
"""

from frappe.model.document import Document


class SprayApplicationBlock(Document):
	pass
