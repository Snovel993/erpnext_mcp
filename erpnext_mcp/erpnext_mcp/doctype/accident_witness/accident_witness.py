# SPDX-License-Identifier: MIT
"""Controller for Accident Witness — one person who saw it.

The only rule worth enforcing on a row somebody types at a scene is that it names
somebody. Everything else — whether their statement has been taken, whether they
work here — is what the investigation is FOR, and a controller that demanded it
up front would be demanding it in the ten minutes when nobody has it.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class AccidentWitness(Document):
	def validate(self):
		self.witness_name = str(self.witness_name or "").strip()
		if not self.witness_name:
			frappe.throw(_("A witness row has to name somebody."))
		if self.statement_taken and not self.statement_taken_on:
			self.statement_taken_on = frappe.utils.now()
