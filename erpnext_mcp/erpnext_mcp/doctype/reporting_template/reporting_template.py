# SPDX-License-Identifier: MIT
"""Controller for Reporting Template — the shape of a periodic report.

THE DOCNAME CARRIES THE COMPANY, for the reason Related Party's does: two
entities on one site file separately, and a template shared between them is a
template neither can edit without changing the other's report. `"10-Q Sections -
OML"` beside `"10-Q Sections - MCF"` is two shapes, which is the truth.

SECTIONS ARE A CHILD TABLE AND NOT A JSON BLOB, which is the opposite of the
choice `Trade Document Template` made for its `required_fields`, and the
difference is worth stating because both are right. That one names fields nobody
queries — a Bill of Lading's columns are rendered, not filtered. These are
walked: the skeleton generator iterates them, the completeness check counts the
required ones, and the MD&A feed matches each against a data source. A blob would
mean parsing to answer "which sections are required", which is a query.

WHAT IS REFUSED. A duplicate section name inside one template, because the
skeleton is keyed on it and two 'Liquidity' sections would silently become one.
A required section is not refused for having no data source: plenty of real
sections are written by a person, and demanding a tool name for the narrative
would be demanding a lie.

TRANSLATION IS OPTIONAL AND ITS ABSENCE IS REPORTED. `label_es` empty means the
English is served and the gap comes back in `untranslated` — the same posture
`list_wizard_definitions` takes, and for the same reason: silently serving
English means nobody finds out until somebody who needed the Spanish did not get
it.
"""

import frappe
from frappe import _
from frappe.model.document import Document

ANNUAL = "Annual"
QUARTERLY = "Quarterly"
MONTHLY = "Monthly"
AD_HOC = "Ad Hoc"


class ReportingTemplate(Document):
	def autoname(self):
		abbr = frappe.db.get_value("Company", self.company, "abbr") or ""
		name = str(self.template_name or "").strip()
		self.name = f"{name} - {abbr}" if abbr else name

	def validate(self):
		self.template_name = str(self.template_name or "").strip()
		if not self.template_name:
			frappe.throw(_("Template Name is required — it is the name a checklist points at."))

		if not str(self.label_en or "").strip():
			# Falls back rather than refusing: the template name is a perfectly
			# good heading, and refusing to save over a display string would cost
			# more than it protects.
			self.label_en = self.template_name

		seen = {}
		for row in self.sections or []:
			key = str(row.section_name or "").strip()
			if not key:
				frappe.throw(_("Row {0}: Section Name is required.").format(row.idx))
			row.section_name = key
			folded = key.casefold()
			if folded in seen:
				frappe.throw(
					_(
						"Rows {0} and {1} are both called {2}. A generated skeleton is keyed on "
						"the section name, so two sections with one name would silently become "
						"one section — and the one that vanished would be the one nobody noticed "
						"was missing."
					).format(seen[folded], row.idx, key)
				)
			seen[folded] = row.idx
			if not str(row.label_en or "").strip():
				row.label_en = key

	def untranslated(self) -> list:
		"""Which display strings have no Spanish. Reported, never guessed at."""
		gaps = []
		if not str(self.label_es or "").strip():
			gaps.append("label_es")
		for row in self.sections or []:
			if not str(row.label_es or "").strip():
				gaps.append(f"sections[{row.idx}].label_es")
		return gaps
