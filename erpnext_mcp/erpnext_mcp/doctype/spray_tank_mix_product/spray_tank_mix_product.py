# SPDX-License-Identifier: MIT
"""Controller for Spray Tank Mix Product — one product's line in one tank.

NOTHING IS VALIDATED HERE AND THAT IS DELIBERATE. Every rule about a product
line is a rule about the line's relationship to the rest of the tank: a rate of
zero, the same Item twice, a nozzle set that no set-A/set-B pair backs. A child
row cannot see its siblings, so a rule enforced here would fire on a row saved
in isolation and stay silent on the case it exists to catch. They live on
`SprayTankMix.validate`, which sees the whole tank.
"""

from frappe.model.document import Document


class SprayTankMixProduct(Document):
	pass
