# SPDX-License-Identifier: MIT
"""Controller for Spray Nozzle Config — one set of tips as it is plumbed.

FLOW IS PER NOZZLE AND IS NOT SILENTLY MULTIPLIED. The manufacturer's chart
gives gallons per minute for one tip at one pressure, and that is what this
column holds. A record that quietly stored boom flow instead would be a record
nobody could check against the chart they have in their hand at the machine, and
the only way to find the error would be to spray at twice the rate.

NOTHING HERE IS COMPUTED INTO A RATE. Gallons per acre depends on ground speed,
which is a property of the pass rather than of the nozzle, so it belongs to
`Spray Application` and is worked out there — see `tools/spray.py:gallons_per_acre`.
Storing a GPA on the master would be storing an answer to a question this record
does not have all of.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class SprayNozzleConfig(Document):
	def validate(self):
		try:
			flow = float(self.flow_rate_gpm or 0)
		except (TypeError, ValueError):
			frappe.throw(_("Flow Rate must be a number."))
		if flow <= 0:
			frappe.throw(
				_(
					"Flow Rate must be greater than zero. A nozzle that flows nothing is not a "
					"configuration, and every rate computed through it would be a division by zero "
					"reported as an acre."
				)
			)
		for label, value in (
			("Rated Pressure", self.rated_pressure_psi),
			("Nozzle Spacing", self.spacing_inches),
			("Boom Width", self.boom_width_ft),
		):
			if value not in (None, "") and float(value) < 0:
				frappe.throw(_("{0} cannot be negative.").format(_(label)))
		if self.nozzles_active not in (None, "") and int(self.nozzles_active) < 0:
			frappe.throw(_("Active Nozzles cannot be negative."))
