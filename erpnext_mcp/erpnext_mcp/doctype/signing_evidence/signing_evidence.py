# SPDX-License-Identifier: MIT
"""Signing Evidence controller — append-only, immutable after insertion.

The same pattern as I-9 Audit Log and MCP Action Log: `before_save` allows the
insert and refuses every subsequent write.

THE REASON IS SHARPER HERE THAN ON THE OTHER TWO LOGS. An audit row that could
be edited is a weakened audit trail; an EVIDENCE row that could be edited is not
evidence at all. The whole claim this doctype makes to an auditor is "these
facts were recorded at the moment of signing and have not been touched since",
and a controller that let one column be corrected would make that sentence
untrue of every row on the site, including the ones nobody corrected.

SO A REPLACED SIGNATURE APPENDS RATHER THAN AMENDS. `collect_form_signature`
with `overwrite=true` writes a NEW evidence row naming the old one in
`supersedes`; the old row keeps saying exactly what it said. `delete` stays with
System Manager so an operator can prune under a retention policy, and Frappe's
own Deleted Document records each deletion.

`in_create` ON THE DOCTYPE IS THE OTHER HALF. It removes the Desk's New button,
so the only way a row exists is the signature path that writes it — which is
what makes the register's completeness mean something.

THE THREE SEAL COLUMNS ARE THE ONE EXCEPTION AND THEY DO NOT COME THROUGH HERE.
v0.63.0 added `sealed_pdf`, `sealed_pdf_hash` and `sealed_at`, which name the
tamper-evident copy this attestation appears in. They are written by
`tools/signed_documents._record_seal` with `frappe.db.set_value(...,
update_modified=False)` — the same door `render_i9_pdf` uses for
`generated_pdf_on` — and NOT through `save()`, so the refusal below stays
absolute for every path a person or a tool could take to revise what this row
says about the signature. The distinction is not a loophole: every other column
is a fact about the moment of signing and is fixed at it, and those three are a
pointer at an artefact produced afterwards that legitimately moves when the form
gains a second signature and is sealed again. See that function, which argues it
at length.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class SigningEvidence(Document):
	def before_save(self):
		if self.flags.in_insert:
			return
		frappe.throw(
			_(
				"Signing Evidence rows are immutable. They record the circumstances of a "
				"signature and cannot be revised — a replaced signature writes a new row that "
				"names this one in Supersedes."
			),
			title=_("Append-Only Evidence"),
		)
