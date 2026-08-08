# SPDX-License-Identifier: MIT
from frappe.model.document import Document


class I9Settings(Document):
	def validate(self):
		if self.enrolled_in_e_verify and not self.store_document_copies:
			self.store_document_copies = 1
