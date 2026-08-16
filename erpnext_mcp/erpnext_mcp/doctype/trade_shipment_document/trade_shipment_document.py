# SPDX-License-Identifier: MIT
"""Controller for Trade Shipment Document — one line of a shipment's checklist.

DELIBERATELY EMPTY OF VALIDATION. A child row's `validate` is not called when its
parent is saved — the same Frappe fact the Wizard Definition controller documents
— so a check written here would be a check that never ran. What has to be true of
a checklist line is enforced in `TradeShipment.validate`, where it does run. This
class exists so the doctype has a controller to import and so this paragraph has
somewhere to live.
"""

from frappe.model.document import Document


class TradeShipmentDocument(Document):
	pass
